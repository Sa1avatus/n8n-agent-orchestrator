// The Guards tab of the dashboard: three views (cards, flow, timelines) over /api/guards and
// /api/guards/history. The shell passes its helpers in mount(): t(key, English), esc(text) and
// setHTML(element, html); the model comes from BoardGuardsView (board_guards_view.js).
(function () {
"use strict";

const V = globalThis.BoardGuardsView;
const REFRESH_MS = 10000;
const VIEWS = ["detail", "cards", "flow", "lanes"];
const WINDOWS = ["1h", "24h", "7d"];
const FLOW_NODE = { width: 150, height: 34, gap: 12, top: 44 };
const FLOW_COLUMN_X = { hosts: 10, guards: 250, receivers: 490 };
const PREFS_KEY = "guards.prefs";
const DETAIL_STRIP = 48;
const DETAIL_ACTIONS = 6;
// what each guard does, shown under its name; a translation key "guard.<name>" wins
const GUARD_TEXT = {
  queue_restart: "Restarts the mission queue when it stops",
  host_heartbeat: "Watches the host watchers",
  tg_relay: "Forwards notices to Telegram",
  mission_notice: "Posts mission start and end notices",
  idle: "Stops idle runs after a timeout",
};

let ctx = null;
let timer = null;
let state = { view: "detail", window: "24h", model: null, host: null, selected: "" };
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

function actionRows(g) {
  const rows = (g.series || []).filter((e) => e.action).slice(-DETAIL_ACTIONS).reverse();
  if (!rows.length) return `<div class="gt-sub">${ctx.esc(ctx.t("none", "—"))}</div>`;
  return rows.map((e) => `<div class="gt-sub gt-mono">${ctx.esc(new Date(e.t * 1000).toLocaleTimeString())} ${ctx.esc(e.action)}</div>`).join("");
}

function settingsCard(g, model) {
  const windows = model.windows.map((w) => `<option value="${ctx.esc(w)}"${w === g.health_window ? " selected" : ""}>${ctx.esc(ctx.t("tab.window." + w, w))}</option>`).join("");
  if (!model.edit) {
    return `<div class="gt-card gt-block"><h4>${ctx.esc(ctx.t("set.title", "Settings"))}</h4><div class="gt-sub">${ctx.esc(ctx.t("set.interval", "Poll interval"))}: ${g.interval_s} ${ctx.esc(ctx.t("unit.s", "s"))}</div>` +
      `<div class="gt-sub">${ctx.esc(ctx.t("set.window", "Health window"))}: ${ctx.esc(g.health_window)}</div><div class="gt-sub">${ctx.esc(ctx.t("set.off", "Editing is off (DASHBOARD_GUARDS_EDIT)."))}</div></div>`;
  }
  return `<div class="gt-card gt-block"><h4>${ctx.esc(ctx.t("set.title", "Settings"))}</h4><div class="gt-fields">` +
    `<label>${ctx.esc(ctx.t("set.interval", "Poll interval"))}<span class="gt-unit"><input type="number" min="1" max="86400" step="1" value="${g.interval_s}" data-field="interval_s"> ${ctx.esc(ctx.t("unit.s", "s"))}</span></label>` +
    `<label>${ctx.esc(ctx.t("set.window", "Health window"))}<select data-field="health_window">${windows}</select></label></div>` +
    `<button type="button" class="gt-save" data-save="${ctx.esc(g.name)}">${ctx.esc(ctx.t("set.save", "Save"))}</button></div>`;
}

function healthCard(g) {
  const h = g.health;
  const pct = h && h.ok_pct != null ? `${h.ok_pct}%` : ctx.t("none", "—");
  const counts = h ? `${h.checks} ${ctx.t("set.checks", "checks")} · ${h.errors} ${ctx.t("error", "error")} · ${ctx.t("set.in", "in")} ${g.health_window}` : "";
  const cells = V.strip(g.series, DETAIL_STRIP).map((r) => `<i class="${ctx.esc(r)}"></i>`).join("");
  return `<div class="gt-card gt-block"><h4>${ctx.esc(ctx.t("set.health", "Health"))}</h4><div class="gt-big">${ctx.esc(pct)}</div><div class="gt-sub">${ctx.esc(counts)}</div><div class="gt-strip">${cells}</div></div>`;
}

function detailView(model, now) {
  const guards = model.guards;
  if (!guards.length) return `<div class="gt-head">${ctx.esc(ctx.t("none", "—"))}</div>`;
  const g = guards.find((x) => x.name === state.selected) || guards[0];
  const items = guards.map((x) => `<button type="button" class="gt-item${x.name === g.name ? " on" : ""}" data-select="${ctx.esc(x.name)}"><span class="gt-led ${ctx.esc(V.statusClass(x.status))}"></span>${ctx.esc(x.name)}</button>`).join("");
  const on = g.status !== "off";
  const toggle = `<button type="button" class="gt-tog${on ? " on" : ""}" role="switch" aria-checked="${on}" aria-label="${ctx.esc(g.name)}" data-toggle="${ctx.esc(g.name)}" data-on="${on}"${model.edit ? "" : " disabled"}></button>`;
  const text = ctx.t("guard." + g.name, GUARD_TEXT[g.name] || "");
  const when = g.last_run ? `${ago(now - g.last_run)} ${ctx.t("tab.ago", "ago")}` : ctx.t("none", "—");
  return `<div class="gt-split"><div class="gt-side">${items}</div><div class="gt-main">` +
    `<div class="gt-dhead"><b class="gt-dname">${ctx.esc(g.name)}</b>${badge(g.status)}${dryMark(g)}<span class="gt-push">${toggle}</span></div>` +
    `<div class="gt-sub">${ctx.esc(text)}${text ? " · " : ""}${ctx.esc(ctx.t("last", "last"))}: ${ctx.esc(when)}</div>` +
    `${settingsCard(g, model)}${healthCard(g)}<div class="gt-card gt-block"><h4>${ctx.esc(ctx.t("set.actions", "Recent actions"))}</h4>${actionRows(g)}</div></div></div>`;
}

function viewHtml(model, now) {
  if (state.view === "detail") return detailView(model, now);
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

function editing() {
  const a = document.activeElement;
  return Boolean(root && a && root.contains(a) && a.matches && a.matches("[data-field]"));
}

async function putGuard(name, body) {
  try {
    const res = await fetch("/api/guards/" + encodeURIComponent(name), {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-AHAWR-Dashboard": "1" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "HTTP " + res.status);
    }
  } catch (e) {
    window.alert(ctx.t("set.failed", "The change was not saved") + ": " + e.message);
  }
  refresh();
}

function saveFields(name) {
  const body = {};
  for (const el of root.querySelectorAll("[data-field]")) {
    body[el.dataset.field] = el.dataset.field === "interval_s" ? Number(el.value) : el.value;
  }
  putGuard(name, body);
}

async function refresh() {
  if (editing()) return;
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
  const pick = e.target.closest("[data-select]");
  const flip = e.target.closest("[data-toggle]");
  const save = e.target.closest("[data-save]");
  if (v) { state.view = v.dataset.view; savePrefs(); render(); }
  if (w) { state.window = w.dataset.window; savePrefs(); refresh(); }
  if (d) hostDecision(d.dataset.host, d.dataset.decision);
  if (pick) { state.selected = pick.dataset.select; render(); }
  if (flip && !flip.disabled) putGuard(flip.dataset.toggle, { enabled: flip.dataset.on !== "true" });
  if (save) saveFields(save.dataset.save);
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
