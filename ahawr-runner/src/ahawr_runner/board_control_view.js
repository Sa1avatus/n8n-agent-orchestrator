// Pure core of the Control tab: turns the /api/board/ops/status and /api/board/ops/commands answers
// into one model for the status strip, the buttons and the history. No DOM; tests/control_view_cases.json
// is shared with tests/board_control_view.test.js. L(key, English) is the translation function.
(function () {
"use strict";

const THRESHOLDS = { agent_stale_seconds: 30 };
const ACTIONS = ["resume", "pause_at_boundary", "stop_mission", "restart_queue"];

function heartbeatAgeS(heartbeatAt, now) {
  if (!heartbeatAt) return null;
  const age = (Date.parse(now) - Date.parse(heartbeatAt)) / 1000;
  return Number.isNaN(age) ? null : age;
}

function agentModel(heartbeatAt, now, L) {
  const ageS = heartbeatAgeS(heartbeatAt, now);
  const alive = ageS !== null && ageS <= THRESHOLDS.agent_stale_seconds;
  return {
    status: alive ? "alive" : "dead",
    heartbeat_age_s: ageS,
    label: alive ? L("control.agent_alive", "Agent alive") : L("control.agent_dead", "Agent not running"),
    banner: alive ? null : L("control.agent_down", "The host agent is not running: its heartbeat is missing or older than 30 seconds. Start it once with the detached start command in docs/board-ops-agent.md; do not start a second one."),
  };
}

function buttonsModel(agentAlive, queueRunning, L) {
  const labels = {
    resume: L("control.button_resume", "Resume"),
    pause_at_boundary: L("control.button_pause_at_boundary", "Pause at boundary"),
    stop_mission: L("control.button_stop_mission", "Stop mission"),
    restart_queue: L("control.button_restart_queue", "Restart queue"),
  };
  const reasons = {
    resume: queueRunning ? L("control.reason_already_running", "The queue is already running") : null,
    pause_at_boundary: queueRunning ? null : L("control.reason_not_running", "The queue is not running"),
    stop_mission: queueRunning ? null : L("control.reason_not_running", "The queue is not running"),
    restart_queue: null,
  };
  const agentReason = agentAlive ? null : L("control.reason_agent_not_running", "The host agent is not running");
  return ACTIONS.map((action) => {
    const reason = agentReason || reasons[action];
    return { action, label: labels[action], enabled: reason === null, disabled_reason: reason };
  });
}

function historyModel(history) {
  const rows = (history && history.commands) || [];
  return rows.map((c) => ({ action: c.action, status: c.status, requested_at: c.requested_at || "" }));
}

function controlModel(status, history, now, L) {
  const queue = (status && status.process_panel && status.process_panel.queue) || {};
  const agent = agentModel(status && status.agent && status.agent.heartbeat_at, now, L);
  const running = !!queue.alive;
  return {
    agent,
    queue: { running, label: running ? L("control.queue_running", "Queue running") : L("control.queue_stopped", "Queue stopped") },
    current: { mission: queue.mission || null, task: queue.task || null },
    buttons: buttonsModel(agent.status === "alive", running, L),
    history: historyModel(history),
  };
}

const BoardControlView = { THRESHOLDS, ACTIONS, controlModel };
if (typeof module !== "undefined" && module.exports) {
  module.exports = BoardControlView;
} else {
  globalThis.BoardControlView = BoardControlView;
}
})();
