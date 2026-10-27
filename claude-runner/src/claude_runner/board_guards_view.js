// Pure core of the Guards tab: turns the /api/guards and /api/guards/history answers into one
// model for the three views (cards, flow, timelines). No DOM; tests/guards_view_cases.json is
// shared with tests/board_guards_view.test.js.
(function () {
"use strict";

const WINDOWS_S = { "1h": 3600, "24h": 86400, "7d": 604800 };
const STRIP_LENGTH = 24;
const LANE_BUCKETS = 100;
const WIRE_HOLD_S = 3;
const RESULT_RANK = { none: 0, ok: 1, warning: 2, error: 3 };
// the status of a guard or heartbeat -> display class and a shape, so colour is never the only signal
const STATUS_CLASS = { ok: "ok", fresh: "ok", warning: "warning", stale: "warning", error: "error", off: "off", unknown: "off" };
const STATUS_SHAPE = { ok: "●", warning: "▲", error: "✕", off: "○" };
// the flow view: which host watcher, guard and receiver is joined to which (static topology)
const FLOW = {
  hosts: ["docker_watchdog", "gpu_power_governor", "queue_launcher"],
  receivers: ["telegram", "n8n", "queue"],
  wires: [
    ["docker_watchdog", "host_heartbeat"],
    ["gpu_power_governor", "host_heartbeat"],
    ["queue_restart", "queue_launcher"],
    ["queue_launcher", "queue"],
    ["tg_relay", "telegram"],
    ["tg_relay", "n8n"],
    ["mission_notice", "telegram"],
    ["idle", "telegram"],
  ],
};

function statusClass(status) { return STATUS_CLASS[status] || "off"; }
function statusShape(status) { return STATUS_SHAPE[statusClass(status)]; }

function seriesOf(history, name) {
  const g = history && history.guards && history.guards[name];
  return Array.isArray(g) ? g : [];
}

function normalise(guards, history) {
  const d = guards || {};
  const list = (d.guards || []).map((g) => ({
    name: g.name,
    status: g.enabled === false ? "off" : (g.status || "ok"),
    dry_run: !!g.dry_run,
    last_run: g.last_run || 0,
    last_action: g.last_action || "",
    last_result: g.last_result || "",
    error_count: g.error_count || 0,
    series: seriesOf(history, g.name),
  }));
  const heartbeats = (d.heartbeats || []).map((h) => ({
    name: h.name,
    status: h.status || "unknown",
    dry_run: !!h.dry_run,
    time: h.time || "",
    last_action: h.last_action || "",
    last_result: h.last_result || "",
  }));
  const count = { ok: 0, warning: 0, error: 0, off: 0 };
  for (const g of list) count[statusClass(g.status)] += 1;
  return {
    enabled: !!d.enabled,
    dry_run: !!d.dry_run,
    running: !!d.running,
    tab: !!d.tab,
    guards: list,
    heartbeats,
    summary: { total: list.length, ok: count.ok, warning: count.warning, error: count.error, off: count.off },
  };
}

// the last STRIP_LENGTH results, padded on the left with "none" (a check that never ran)
function strip(series, length) {
  const n = length || STRIP_LENGTH;
  const last = (series || []).slice(-n).map((e) => (e.result === "error" ? "error" : e.result === "warning" ? "warning" : "ok"));
  while (last.length < n) last.unshift("none");
  return last;
}

// dots on a lane: a position in percent per check; checks that fall into one bucket merge and the
// worst result wins; a bucket with an action is marked
function lanes(series, windowName, now) {
  const span = WINDOWS_S[windowName] || WINDOWS_S["24h"];
  const since = now - span;
  const buckets = new Map();
  for (const e of series || []) {
    if (e.t < since || e.t > now) continue;
    const slot = Math.min(LANE_BUCKETS - 1, Math.floor(((e.t - since) / span) * LANE_BUCKETS));
    const prev = buckets.get(slot) || { result: "none", action: false, count: 0 };
    const result = RESULT_RANK[e.result] > RESULT_RANK[prev.result] ? e.result : prev.result;
    buckets.set(slot, { result, action: prev.action || !!e.action, count: prev.count + 1 });
  }
  return [...buckets.entries()].sort((a, b) => a[0] - b[0]).map(([slot, b]) => ({
    pct: Math.round(((slot + 0.5) / LANE_BUCKETS) * 1000) / 10,
    result: b.result,
    action: b.action,
    count: b.count,
  }));
}

// a wire is lit for WIRE_HOLD_S seconds after its source acted
function activeSources(model, now) {
  const lit = new Set();
  for (const g of model.guards) {
    if (g.last_action && g.last_run && now - g.last_run <= WIRE_HOLD_S) lit.add(g.name);
  }
  return lit;
}

function flowModel(model, now) {
  const byName = {};
  for (const g of model.guards) byName[g.name] = g.status;
  for (const h of model.heartbeats) byName[h.name] = h.status;
  const lit = activeSources(model, now);
  const node = (id) => ({ id, status: byName[id] || "unknown" });
  const guardNames = model.guards.map((g) => g.name);
  return {
    hosts: FLOW.hosts.map(node),
    guards: guardNames.map(node),
    receivers: FLOW.receivers.map((id) => ({ id, status: "unknown" })),
    wires: FLOW.wires.map(([from, to]) => ({ from, to, active: lit.has(from) })),
  };
}

// the host-actions panel: Confirm for a pending CONFIRM-tier request, Reject for a pending or confirmed one;
// AUTO and not-whitelisted rows get no buttons
function hostActionsModel({ enabled, requests, now }, L) {
  if (!enabled) return { visible: false, rows: [] };
  const rows = (requests || []).map((r) => {
    const operatorTier = r.tier === "CONFIRM";
    return {
      id: r.id,
      state: r.state,
      service: r.service,
      action: r.action,
      reason: r.reason,
      age_s: Math.max(0, Math.round(now - Date.parse(r.created_at) / 1000)),
      confirm: operatorTier && r.state === "pending",
      reject: operatorTier && (r.state === "pending" || r.state === "confirmed"),
    };
  });
  return {
    visible: true,
    title: L("host.title", "Host actions"),
    confirm: L("host.confirm", "Confirm"),
    reject: L("host.reject", "Reject"),
    rows,
  };
}

const BoardGuardsView = { WINDOWS_S, STRIP_LENGTH, FLOW, statusClass, statusShape, normalise, strip, lanes, flowModel, activeSources, hostActionsModel };
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardGuardsView;
} else if (typeof document !== "undefined") {
  globalThis.BoardGuardsView = BoardGuardsView;
}
})();
