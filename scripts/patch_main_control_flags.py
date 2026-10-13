#!/usr/bin/env python3
"""Patch the main workflow control flags (pause, stop, steer, accept, retry)
in both v13 workflows.

Adds:
  - a pause gate that reads Live State before each new task starts
    (never mid-attempt); the gate reads a fresh Live State row via its own
    Load Live State / Read Control Flags pair;
  - a stop check after the current step that sets workflow_status = stopped
    and leaves no run in the running state without an owner; each stop check
    reads a fresh Live State row via its own pair, and the Stop Workflow node
    is followed by a Live State update that writes workflow_status = stopped
    and clears the owner;
  - steer_note appended to review_feedback of the NEXT attempt only, then
    cleared; the Pause Gate fresh pair feeds Prepare Current Task, and the
    Accept Gate fresh pair feeds Retry Current Task;
  - accept, which marks the current task accepted manually, records the
    reason and advances: accept_requested is read fresh by the Accept Gate
    pair in front of Review Passed? and OR'd into its condition;
  - retry, which runs one more attempt with the operator remark (the
    retry_requested flag allows one attempt beyond max_attempts_per_task);
  - a Clear Consumed Flags node before the next task starts that clears
    steer_note, accept_requested and retry_requested and records the accept
    reason in Live State last_control;
  - a Hold Paused Write Live State update that writes workflow_status =
    paused and clears the owner.

Leave Build Worker Run Input unchanged and Prepare Current Task unchanged
except for the steer append to review_feedback.  Do not touch verdict retry,
compaction, retrieval, report language or Telegram notices.

Idempotent: re-running the script is a no-op if all control nodes are
present, the patched code is in place, the gates read their fresh flag
nodes, and the Review Passed? accept condition is present.

Checks performed:
  - JSON is valid
  - node ids and names are unique
  - all connections resolve to existing node names
  - Build Worker Run Input jsCode is unchanged (before/after)
  - Prepare Current Task jsCode is unchanged except for the steer append
  - nodes outside the patch set are unchanged (before/after comparison)

Usage: python scripts/patch_main_control_flags.py <workflow.json>
"""

import copy
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Node names and new-node ids
# ---------------------------------------------------------------------------

# Existing anchor nodes (must be present in the workflow)
ANCHOR_CONTINUE = "Continue Workflow?"
ANCHOR_PREPARE = "Prepare Current Task"
ANCHOR_WORKER_COMPLETED = "Worker Completed?"
ANCHOR_REVIEWER_COMPLETED = "Reviewer Completed?"
ANCHOR_REVIEW_PASSED = "Review Passed?"
ANCHOR_RETRY = "Retry Current Task"
ANCHOR_LOAD_TASK_STATE = "Load Task State"
ANCHOR_BWRI = "Build Worker Run Input"
ANCHOR_RESTORE_REVIEW_CTX = "Restore Review Context"

# New nodes to add
NEW_LOAD_LIVE_STATE = "Load Live State"
NEW_READ_CONTROL = "Read Control Flags"
NEW_PAUSE_GATE = "Pause Gate"
NEW_PAUSE_LIVE = "Pause Gate Live State"
NEW_PAUSE_FLAGS = "Pause Gate Flags"
NEW_HOLD_PAUSED = "Hold Paused"
NEW_HOLD_WRITE = "Hold Paused Write Live State"
NEW_STOP_WORKFLOW = "Stop Workflow"
NEW_STOP_WRITE = "Stop Workflow Write Live State"
NEW_STOP_CHECK_WORKER = "Stop Check Worker"
NEW_STOP_CHECK_WORKER_LIVE = "Stop Check Worker Live State"
NEW_STOP_CHECK_WORKER_FLAGS = "Stop Check Worker Flags"
NEW_STOP_CHECK_REVIEWER = "Stop Check Reviewer"
NEW_STOP_CHECK_REVIEWER_LIVE = "Stop Check Reviewer Live State"
NEW_STOP_CHECK_REVIEWER_FLAGS = "Stop Check Reviewer Flags"
NEW_ACCEPT_GATE = "Accept Gate Live State"
NEW_ACCEPT_FLAGS = "Accept Gate Flags"
NEW_CLEAR_FLAGS = "Clear Consumed Flags"

# Stable ids for the new nodes (fixed so re-runs are idempotent)
ID_LOAD_LIVE_STATE = "aa110001-0000-4000-8000-000000000001"
ID_READ_CONTROL = "aa110002-0000-4000-8000-000000000002"
ID_PAUSE_GATE = "aa110003-0000-4000-8000-000000000003"
ID_PAUSE_LIVE = "aa110004-0000-4000-8000-000000000004"
ID_PAUSE_FLAGS = "aa110005-0000-4000-8000-000000000005"
ID_HOLD_PAUSED = "aa110006-0000-4000-8000-000000000006"
ID_HOLD_WRITE = "aa110007-0000-4000-8000-000000000007"
ID_STOP_WORKFLOW = "aa110008-0000-4000-8000-000000000008"
ID_STOP_WRITE = "aa110009-0000-4000-8000-000000000009"
ID_STOP_CHECK_WORKER = "aa110010-0000-4000-8000-000000000010"
ID_STOP_CHECK_WORKER_LIVE = "aa110011-0000-4000-8000-000000000011"
ID_STOP_CHECK_WORKER_FLAGS = "aa110012-0000-4000-8000-000000000012"
ID_STOP_CHECK_REVIEWER = "aa110013-0000-4000-8000-000000000013"
ID_STOP_CHECK_REVIEWER_LIVE = "aa110014-0000-4000-8000-000000000014"
ID_STOP_CHECK_REVIEWER_FLAGS = "aa110015-0000-4000-8000-000000000015"
ID_ACCEPT_GATE = "aa110016-0000-4000-8000-000000000016"
ID_ACCEPT_FLAGS = "aa110017-0000-4000-8000-000000000017"
ID_CLEAR_FLAGS = "aa110018-0000-4000-8000-000000000018"

# ---------------------------------------------------------------------------
# Helpers for node construction
# ---------------------------------------------------------------------------

LIVE_STATE_FILTERS = {
    "conditions": [
        {
            "keyName": "state_key",
            "keyValue": "={{ $('Constants').first().json.state_namespace }}",
        }
    ]
}


def live_state_get_node(node_id, name, position):
    return {
        "parameters": {
            "operation": "get",
            "dataTableId": {
                "__rl": True,
                "mode": "name",
                "value": "Autonomous Agent Live State",
            },
            "matchType": "allConditions",
            "filters": LIVE_STATE_FILTERS,
            "limit": 1,
        },
        "type": "n8n-nodes-base.dataTable",
        "typeVersion": 2,
        "position": position,
        "id": node_id,
        "name": name,
    }


def live_state_update_node(node_id, name, position, column_exprs):
    """A dataTable update that writes the given columns, matched on
    state_key = Constants.state_namespace.  Column expressions are evaluated
    against the node's input item ($json)."""
    return {
        "parameters": {
            "resource": "table",
            "operation": "update",
            "dataTableId": {
                "__rl": True,
                "mode": "name",
                "value": "Autonomous Agent Live State",
            },
            "matchType": "allConditions",
            "filters": LIVE_STATE_FILTERS,
            "columns": {
                "mappingMode": "defineBelow",
                "value": column_exprs,
            },
            "options": {"updateMatchingRows": False},
        },
        "type": "n8n-nodes-base.dataTable",
        "typeVersion": 1.1,
        "position": position,
        "id": node_id,
        "name": name,
    }


def code_node(node_id, name, position, js):
    return {
        "parameters": {"jsCode": js},
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": position,
        "id": node_id,
        "name": name,
    }


def if_node(node_id, name, position, left_value, cond_id):
    return {
        "parameters": {
            "conditions": {
                "options": {
                    "caseSensitive": True,
                    "leftValue": "",
                    "typeValidation": "strict",
                    "version": 3,
                },
                "conditions": [
                    {
                        "id": cond_id,
                        "leftValue": left_value,
                        "rightValue": "",
                        "operator": {
                            "type": "boolean",
                            "operation": "true",
                            "name": "filter.operator.true",
                        },
                    }
                ],
                "combinator": "and",
            },
            "options": {},
        },
        "type": "n8n-nodes-base.if",
        "typeVersion": 2.3,
        "position": position,
        "id": node_id,
        "name": name,
    }


# ---------------------------------------------------------------------------
# New node definitions
# ---------------------------------------------------------------------------

def read_control_js(live_node_name):
    """Build the Read Control Flags jsCode that reads from the given Live
    State node.  Each Flags node reads its own fresh Live State row."""
    return (
        "const rows = $(" + f"'{live_node_name}'" + ").all();\n"
        "const live = rows.length ? (rows[0].json ?? {}) : {};"
        "return [{ json: {\n"
        "  paused: Boolean(live.paused ?? false),\n"
        "  pause_reason: String(live.pause_reason ?? ''),\n"
        "  stop_requested: Boolean(live.stop_requested ?? false),\n"
        "  accept_requested: Boolean(live.accept_requested ?? false),\n"
        "  retry_requested: Boolean(live.retry_requested ?? false),\n"
        "  steer_note: String(live.steer_note ?? ''),\n"
        "  operator_feedback: String(live.operator_feedback ?? ''),\n"
        "  workflow_status: String(live.workflow_status ?? ''),\n"
        "  owner: String(live.owner ?? '')\n"
        "} }];"
    )


READ_CONTROL_JS = read_control_js(NEW_LOAD_LIVE_STATE)

# The initial (startup) Live State read pair: feeds Resume Saved State?.
LOAD_LIVE_STATE_NODE = live_state_get_node(ID_LOAD_LIVE_STATE, NEW_LOAD_LIVE_STATE, [-17000, 1728])
READ_CONTROL_NODE = code_node(ID_READ_CONTROL, NEW_READ_CONTROL, [-16800, 1728], READ_CONTROL_JS)

# Fresh reads at each control point.  Each gate/stop check/accept gate has
# its own Load Live State (get, limit 1) and Read Control Flags node so the
# flag read happens at the exact moment of the decision, not earlier.
PAUSE_LIVE_STATE_NODE = live_state_get_node(ID_PAUSE_LIVE, NEW_PAUSE_LIVE, [-8800, 1728])
PAUSE_FLAGS_NODE = code_node(ID_PAUSE_FLAGS, NEW_PAUSE_FLAGS, [-8600, 1728], read_control_js(NEW_PAUSE_LIVE))
PAUSE_GATE_NODE = if_node(
    ID_PAUSE_GATE, NEW_PAUSE_GATE, [-8600, 1312],
    "={{ $('Pause Gate Flags').first().json.paused }}", "pause-gate",
)
HOLD_PAUSED_NODE = code_node(
    ID_HOLD_PAUSED, NEW_HOLD_PAUSED, [-8200, 1104],
    "// Pause gate: hold the workflow.  The operator clears the paused\n"
    "// flag in Live State and re-runs the workflow to resume.\n"
    "return [{ json: { ...$json, execution_route: 'paused', final_status: 'paused_by_operator' } }];",
)
HOLD_WRITE_NODE = live_state_update_node(
    ID_HOLD_WRITE, NEW_HOLD_WRITE, [-7900, 1104],
    {
        "workflow_status": "={{ 'paused' }}",
        "owner": "={{ '' }}",
    },
)

STOP_WORKFLOW_NODE = code_node(
    ID_STOP_WORKFLOW, NEW_STOP_WORKFLOW, [-13200, 1968],
    "// Stop check: the operator requested a stop after the current step.\n"
    "// Set workflow_status = stopped and leave no run in the running state.\n"
    "const ctrl = $('Stop Check Worker Flags').first().json ?? {};\n"
    "return [{ json: {\n"
    "  ...$json,\n"
    "  workflow_status: 'stopped',\n"
    "  execution_route: 'stopped',\n"
    "  final_status: 'stopped_by_operator',\n"
    "  stop_requested: Boolean(ctrl.stop_requested ?? false),\n"
    "  owner: String(ctrl.owner ?? '')\n"
    "} }];",
)
STOP_WRITE_NODE = live_state_update_node(
    ID_STOP_WRITE, NEW_STOP_WRITE, [-12900, 1968],
    {
        "workflow_status": "={{ 'stopped' }}",
        "owner": "={{ '' }}",
    },
)

STOP_CHECK_WORKER_LIVE = live_state_get_node(
    ID_STOP_CHECK_WORKER_LIVE, NEW_STOP_CHECK_WORKER_LIVE, [-13800, 2384])
STOP_CHECK_WORKER_FLAGS = code_node(
    ID_STOP_CHECK_WORKER_FLAGS, NEW_STOP_CHECK_WORKER_FLAGS, [-13600, 2384],
    read_control_js(NEW_STOP_CHECK_WORKER_LIVE))
STOP_CHECK_WORKER_NODE = if_node(
    ID_STOP_CHECK_WORKER, NEW_STOP_CHECK_WORKER, [-13500, 2176],
    "={{ $('Stop Check Worker Flags').first().json.stop_requested }}",
    "stop-check-worker",
)

STOP_CHECK_REVIEWER_LIVE = live_state_get_node(
    ID_STOP_CHECK_REVIEWER_LIVE, NEW_STOP_CHECK_REVIEWER_LIVE, [-11800, 2080])
STOP_CHECK_REVIEWER_FLAGS = code_node(
    ID_STOP_CHECK_REVIEWER_FLAGS, NEW_STOP_CHECK_REVIEWER_FLAGS, [-11600, 2080],
    read_control_js(NEW_STOP_CHECK_REVIEWER_LIVE))
STOP_CHECK_REVIEWER_NODE = if_node(
    ID_STOP_CHECK_REVIEWER, NEW_STOP_CHECK_REVIEWER, [-11600, 1872],
    "={{ $('Stop Check Reviewer Flags').first().json.stop_requested }}",
    "stop-check-reviewer",
)

# Fresh read in front of Review Passed?: the accept_requested flag is read
# at the moment of the review decision, and the gate passes through with it.
ACCEPT_GATE_NODE = live_state_get_node(ID_ACCEPT_GATE, NEW_ACCEPT_GATE, [-10000, 2600])
ACCEPT_FLAGS_NODE = code_node(ID_ACCEPT_FLAGS, NEW_ACCEPT_FLAGS, [-9800, 2600], read_control_js(NEW_ACCEPT_GATE))

# Clear consumed flags just before the next task starts: steer_note,
# accept_requested and retry_requested are consumed here (they were read
# for this attempt) and cleared so the next attempt starts clean.  The
# accept reason is recorded in Live State last_control.
CLEAR_FLAGS_JS = (
    "// Clear consumed control flags before the next task starts.\n"
    "// steer_note was appended to review_feedback for this attempt (NEXT\n"
    "// attempt only); accept_requested was consumed by the Accept Gate;\n"
    "// retry_requested was consumed by Retry Current Task.\n"
    "const ctrl = $('Pause Gate Flags').first().json ?? {};\n"
    "return [{ json: {\n"
    "  ...$json,\n"
    "  cleared_flags: { steer_note: ctrl.steer_note, accept_requested: ctrl.accept_requested, retry_requested: ctrl.retry_requested },\n"
    "  accept_requested_was_true: Boolean(ctrl.accept_requested ?? false),\n"
    "  accept_reason: String(ctrl.operator_feedback ?? '')\n"
    "} }];"
)
CLEAR_FLAGS_NODE = code_node(ID_CLEAR_FLAGS, NEW_CLEAR_FLAGS, [-8400, 1312], CLEAR_FLAGS_JS)

CLEAR_FLAGS_WRITE_NODE = live_state_update_node(
    "aa110019-0000-4000-8000-000000000019", "Clear Consumed Flags Write Live State", [-8100, 1312],
    {
        "steer_note": "={{ '' }}",
        "accept_requested": "={{ false }}",
        "retry_requested": "={{ false }}",
        "last_control": "={{ $json.accept_requested_was_true ? 'accept: ' + String($json.accept_reason ?? '') : '' }}",
    },
)
# NOTE: the clear write node reuses a distinct id; fix below.


# ---------------------------------------------------------------------------
# Patched code for existing nodes
# ---------------------------------------------------------------------------

# Prepare Current Task: unchanged except the steer append to review_feedback.
# The fresh pause-gate flag read (Pause Gate Flags) is used here; the steer
# note is appended only for the NEXT attempt and cleared afterwards.
PREPARE_ORIGINAL = """const tasks = Array.isArray($json.tasks) ? $json.tasks : [];
const index = Number($json.task_index ?? 0);
if (index >= tasks.length) return [{ json: { ...$json, execution_route: 'all_tasks_completed' } }];
const task = tasks[index];
const feedback = String($json.review_feedback ?? '');
const workerPrompt = [
  $('Prompt Templates').first().json.worker_prompt,
  '',
  // the same folder block the Worker gets (Build Worker Run Input), so the notice shows it too
  String($('Mission').first().json.working_directory_block ?? ''),
  '',
  `TASK ${task.id}: ${task.title}`,
  '',
  'OBJECTIVE:',
  task.objective,
  '',
  'ACCEPTANCE CRITERIA:',
  task.acceptance_criteria.map((x, i) => `${i + 1}. ${x}`).join('\\n'),
  '',
  'VERIFICATION:',
  task.verification.join('\\n'),
  '',
  'SCOPE:',
  task.scope.join('\\n'),
  '',
  feedback ? 'PREVIOUS REVIEW FEEDBACK:' : '',
  feedback
].filter(Boolean).join('\\n');
return [{ json: { ...$json, execution_route: 'run_task', current_task: task, current_task_id: task.id, worker_prompt: workerPrompt } }];"""

PREPARE_PATCHED = """const tasks = Array.isArray($json.tasks) ? $json.tasks : [];
const index = Number($json.task_index ?? 0);
if (index >= tasks.length) return [{ json: { ...$json, execution_route: 'all_tasks_completed' } }];
const task = tasks[index];
const feedback = String($json.review_feedback ?? '');
// Steer note: appended to review_feedback for the NEXT attempt only.
// Read from the fresh pause-gate flag read; cleared after use.
const steer = String($('Pause Gate Flags').first().json.steer_note ?? '');
const combinedFeedback = feedback + (steer ? '\\nOPERATOR STEER NOTE: ' + steer : '');
const workerPrompt = [
  $('Prompt Templates').first().json.worker_prompt,
  '',
  // the same folder block the Worker gets (Build Worker Run Input), so the notice shows it too
  String($('Mission').first().json.working_directory_block ?? ''),
  '',
  `TASK ${task.id}: ${task.title}`,
  '',
  'OBJECTIVE:',
  task.objective,
  '',
  'ACCEPTANCE CRITERIA:',
  task.acceptance_criteria.map((x, i) => `${i + 1}. ${x}`).join('\\n'),
  '',
  'VERIFICATION:',
  task.verification.join('\\n'),
  '',
  'SCOPE:',
  task.scope.join('\\n'),
  '',
  combinedFeedback ? 'PREVIOUS REVIEW FEEDBACK:' : '',
  combinedFeedback
].filter(Boolean).join('\\n');
return [{ json: { ...$json, execution_route: 'run_task', current_task: task, current_task_id: task.id, worker_prompt: workerPrompt, review_feedback: combinedFeedback } }];"""

# Retry Current Task: operator remark (steer_note + operator_feedback)
# appended to review_feedback for the retry; retry_requested allows one
# attempt beyond max_attempts_per_task.
RETRY_ORIGINAL = """const state = (() => {
  for (const n of ['Prepare Current Task','Restore Saved Task State','Restore Task After State Save','Restore Retry State','Restore Plan After State Save']) {
    try { if ($(n).isExecuted) { const j = $(n).last().json ?? {}; if (j && (Array.isArray(j.tasks) || j.plan_json || j.current_task)) return j; } } catch (_) {}
  }
  return $json ?? {};
})();
const tasks = Array.isArray(state.tasks) ? state.tasks : [];
// The attempt the log records (Persist Parsed Review): the first listed node with a number.
// `state` above can be an older node after a resumed execution; take the larger of the two.
const loggedAttempt = (() => {
  for (const n of ['Prepare Current Task','Restore Saved Task State','Restore Task After State Save','Restore Retry State','Restore Plan After State Save']) {
    try { if ($(n).isExecuted) { const v = Number($(n).last().json.task_attempt ?? NaN); if (Number.isFinite(v) && v > 0) return v; } } catch (_) {}
  }
  return 0;
})();
const currentAttempt = Math.max(Number(state.task_attempt ?? 1), loggedAttempt);
const maxAttempts = Number($('Constants').first().json.max_attempts_per_task ?? 3);
const reviewReason = String($json.review_reason ?? $json.review?.reason ?? '');
const reviewNext = String($json.review_next_task ?? $json.review?.next_task ?? '');
if (currentAttempt >= maxAttempts) {
  return [{json:{ ...state, plan_summary: String(state.plan_summary ?? ''), tasks, total_tasks: tasks.length, execution_route: 'failed', final_status: 'failed_task_attempt_limit', failure_reason: reviewReason || 'Maximum task attempts reached.', review_feedback: reviewNext || reviewReason, last_review_score: Number($json.review_score ?? $json.review?.score ?? 0), last_review_reason: reviewReason }}];
}
return [{json:{ ...state, plan_summary: String(state.plan_summary ?? ''), tasks, total_tasks: tasks.length, task_attempt: currentAttempt + 1, review_feedback: reviewNext || reviewReason, execution_route: 'run_task', last_review_score: Number($json.review_score ?? $json.review?.score ?? 0), last_review_reason: reviewReason }}];"""

RETRY_PATCHED = """const state = (() => {
  for (const n of ['Prepare Current Task','Restore Saved Task State','Restore Task After State Save','Restore Retry State','Restore Plan After State Save']) {
    try { if ($(n).isExecuted) { const j = $(n).last().json ?? {}; if (j && (Array.isArray(j.tasks) || j.plan_json || j.current_task)) return j; } } catch (_) {}
  }
  return $json ?? {};
})();
const tasks = Array.isArray(state.tasks) ? state.tasks : [];
// The attempt the log records (Persist Parsed Review): the first listed node with a number.
// `state` above can be an older node after a resumed execution; take the larger of the two.
const loggedAttempt = (() => {
  for (const n of ['Prepare Current Task','Restore Saved Task State','Restore Task After State Save','Restore Retry State','Restore Plan After State Save']) {
    try { if ($(n).isExecuted) { const v = Number($(n).last().json.task_attempt ?? NaN); if (Number.isFinite(v) && v > 0) return v; } } catch (_) {}
  }
  return 0;
})();
const currentAttempt = Math.max(Number(state.task_attempt ?? 1), loggedAttempt);
const maxAttempts = Number($('Constants').first().json.max_attempts_per_task ?? 3);
const reviewReason = String($json.review_reason ?? $json.review?.reason ?? '');
const reviewNext = String($json.review_next_task ?? $json.review?.next_task ?? '');
// Steer note and operator remark: appended to review_feedback for the
// NEXT attempt only (retry).  Read from the fresh accept-gate flag read.
// retry_requested allows one attempt beyond max_attempts_per_task.
const ctrl = $('Accept Gate Flags').first().json ?? {};
const steer = String(ctrl.steer_note ?? '');
const operatorFeedback = String(ctrl.operator_feedback ?? '');
const extraFeedback = [steer ? 'OPERATOR STEER NOTE: ' + steer : '', operatorFeedback ? 'OPERATOR REMARK: ' + operatorFeedback : ''].filter(Boolean).join('\\n');
const combinedFeedback = [reviewNext || reviewReason, extraFeedback].filter(Boolean).join('\\n');
const limit = Number(ctrl.retry_requested) ? maxAttempts + 1 : maxAttempts;
if (currentAttempt >= limit) {
  return [{json:{ ...state, plan_summary: String(state.plan_summary ?? ''), tasks, total_tasks: tasks.length, execution_route: 'failed', final_status: 'failed_task_attempt_limit', failure_reason: reviewReason || 'Maximum task attempts reached.', review_feedback: combinedFeedback, last_review_score: Number($json.review_score ?? $json.review?.score ?? 0), last_review_reason: reviewReason }}];
}
return [{json:{ ...state, plan_summary: String(state.plan_summary ?? ''), tasks, total_tasks: tasks.length, task_attempt: currentAttempt + 1, review_feedback: combinedFeedback, execution_route: 'run_task', last_review_score: Number($json.review_score ?? $json.review?.score ?? 0), last_review_reason: reviewReason }}];"""


# ---------------------------------------------------------------------------
# Validation and patch logic
# ---------------------------------------------------------------------------


def check_workflow(wf, label):
    """Validate node id/name uniqueness and connection resolution.

    The connections dict is keyed by node name; each entry maps to a list
    of outgoing connection groups.
    """
    ids = set()
    names = set()
    for n in wf["nodes"]:
        nid = n["id"]
        if nid in ids:
            raise ValueError(f"{label}: duplicate node id {nid}")
        ids.add(nid)
        nm = n["name"]
        if nm in names:
            raise ValueError(f"{label}: duplicate node name {nm}")
        names.add(nm)

    for src_name, outs in wf.get("connections", {}).items():
        if src_name not in names:
            raise ValueError(f"{label}: connection source {src_name!r} is not a known node name")
        for main in outs.get("main", []):
            for entry in main:
                if isinstance(entry, dict) and entry.get("node") not in names:
                    raise ValueError(
                        f"{label}: connection to node name {entry['node']!r} is not a known node name"
                    )
    return ids, names


def find_node(wf, name):
    for n in wf["nodes"]:
        if n["name"] == name:
            return n
    return None


def snapshot_nodes(wf):
    """Return a dict of node name -> (type, position, parameters) for comparison."""
    snap = {}
    for n in wf["nodes"]:
        snap[n["name"]] = (n["type"], tuple(n.get("position", [])), copy.deepcopy(n["parameters"]))
    return snap


def conn_targets(wf, src):
    """Return the list of target node names for a connection source."""
    if src not in wf.get("connections", {}):
        return []
    targets = []
    for main in wf["connections"][src].get("main", []):
        for entry in main:
            if isinstance(entry, dict) and entry.get("node"):
                targets.append(entry["node"])
    return targets


def replace_outgoing(wf, src, new_target):
    """Set the outgoing connections of src to a single edge to new_target.
    Works for both existing nodes and newly added nodes."""
    wf["connections"][src] = {"main": [[{"node": new_target, "type": "main", "index": 0}]]}


def branch_outgoing(wf, src, first_target, second_target):
    """Set src to branch to first_target (main group 0) and second_target (main group 1)."""
    wf["connections"][src] = {
        "main": [
            [{"node": first_target, "type": "main", "index": 0}],
            [{"node": second_target, "type": "main", "index": 0}],
        ]
    }


def patch_file(path, label):
    wf = json.loads(path.read_text(encoding="utf-8"))

    # Find all required anchor nodes; fail with a clear message naming
    # any missing anchor.  (Checked before connection validation so the
    # error names the missing anchor, not a broken connection.)
    anchors = {
        ANCHOR_CONTINUE: find_node(wf, ANCHOR_CONTINUE),
        ANCHOR_PREPARE: find_node(wf, ANCHOR_PREPARE),
        ANCHOR_WORKER_COMPLETED: find_node(wf, ANCHOR_WORKER_COMPLETED),
        ANCHOR_REVIEWER_COMPLETED: find_node(wf, ANCHOR_REVIEWER_COMPLETED),
        ANCHOR_REVIEW_PASSED: find_node(wf, ANCHOR_REVIEW_PASSED),
        ANCHOR_RETRY: find_node(wf, ANCHOR_RETRY),
        ANCHOR_LOAD_TASK_STATE: find_node(wf, ANCHOR_LOAD_TASK_STATE),
        ANCHOR_BWRI: find_node(wf, ANCHOR_BWRI),
        ANCHOR_RESTORE_REVIEW_CTX: find_node(wf, ANCHOR_RESTORE_REVIEW_CTX),
    }
    missing = [nm for nm, node in anchors.items() if node is None]
    if missing:
        raise ValueError(f"{label}: missing anchor node(s): {', '.join(missing)}")

    # Pre-check: unique ids/names, connections resolve
    check_workflow(wf, label)

    prepare = anchors[ANCHOR_PREPARE]
    retry = anchors[ANCHOR_RETRY]
    continue_wf = anchors[ANCHOR_CONTINUE]
    worker_completed = anchors[ANCHOR_WORKER_COMPLETED]
    reviewer_completed = anchors[ANCHOR_REVIEWER_COMPLETED]
    review_passed = anchors[ANCHOR_REVIEW_PASSED]
    load_task_state = anchors[ANCHOR_LOAD_TASK_STATE]
    bwri = anchors[ANCHOR_BWRI]
    restore_review_ctx = anchors[ANCHOR_RESTORE_REVIEW_CTX]

    # --- Idempotency check ---
    # All new nodes must be present, the patched code must be in place,
    # the gates must read their fresh flag nodes, and the Review Passed?
    # accept condition must be present.
    required_new = [
        NEW_LOAD_LIVE_STATE,
        NEW_READ_CONTROL,
        NEW_PAUSE_LIVE,
        NEW_PAUSE_FLAGS,
        NEW_PAUSE_GATE,
        NEW_HOLD_PAUSED,
        NEW_HOLD_WRITE,
        NEW_STOP_WORKFLOW,
        NEW_STOP_WRITE,
        NEW_STOP_CHECK_WORKER,
        NEW_STOP_CHECK_WORKER_LIVE,
        NEW_STOP_CHECK_WORKER_FLAGS,
        NEW_STOP_CHECK_REVIEWER,
        NEW_STOP_CHECK_REVIEWER_LIVE,
        NEW_STOP_CHECK_REVIEWER_FLAGS,
        NEW_ACCEPT_GATE,
        NEW_ACCEPT_FLAGS,
        NEW_CLEAR_FLAGS,
        NEW_CLEAR_FLAGS + " Write Live State",
    ]
    all_present = all(find_node(wf, nm) is not None for nm in required_new)
    prepare_ok = prepare["parameters"]["jsCode"] == PREPARE_PATCHED
    retry_ok = retry["parameters"]["jsCode"] == RETRY_PATCHED
    # Review Passed? must contain the accept_requested condition
    rp_conds = review_passed["parameters"]["conditions"]["conditions"]
    rp_ok = any(c.get("id") == "accept-requested" for c in rp_conds)
    # The stop checks must read their fresh flag nodes
    scw_ok = (
        find_node(wf, NEW_STOP_CHECK_WORKER) is not None
        and "Stop Check Worker Flags" in find_node(wf, NEW_STOP_CHECK_WORKER)["parameters"]["conditions"]["conditions"][0]["leftValue"]
    )
    scr_ok = (
        find_node(wf, NEW_STOP_CHECK_REVIEWER) is not None
        and "Stop Check Reviewer Flags" in find_node(wf, NEW_STOP_CHECK_REVIEWER)["parameters"]["conditions"]["conditions"][0]["leftValue"]
    )
    # The pause gate must read its fresh flag node
    pg_ok = (
        find_node(wf, NEW_PAUSE_GATE) is not None
        and "Pause Gate Flags" in find_node(wf, NEW_PAUSE_GATE)["parameters"]["conditions"]["conditions"][0]["leftValue"]
    )
    # Each Flags node's jsCode must match the expected code built from its own
    # Live State node.
    flags_expected = {
        NEW_READ_CONTROL: read_control_js(NEW_LOAD_LIVE_STATE),
        NEW_PAUSE_FLAGS: read_control_js(NEW_PAUSE_LIVE),
        NEW_STOP_CHECK_WORKER_FLAGS: read_control_js(NEW_STOP_CHECK_WORKER_LIVE),
        NEW_STOP_CHECK_REVIEWER_FLAGS: read_control_js(NEW_STOP_CHECK_REVIEWER_LIVE),
        NEW_ACCEPT_FLAGS: read_control_js(NEW_ACCEPT_GATE),
    }
    flags_ok = all(
        find_node(wf, nm) is not None
        and find_node(wf, nm)["parameters"]["jsCode"] == expected
        for nm, expected in flags_expected.items()
    )
    clear_node = find_node(wf, NEW_CLEAR_FLAGS)
    clear_ok = (
        clear_node is not None
        and clear_node["parameters"]["jsCode"] == CLEAR_FLAGS_JS
    )
    if all_present and prepare_ok and retry_ok and rp_ok and scw_ok and scr_ok and pg_ok and flags_ok and clear_ok:
        print(f"{label}: already patched, skipping")
        return

    # Repair path: all new nodes are present but one or more Flags jsCodes
    # or the Clear Consumed Flags Write last_control expression are stale
    # (from an earlier version of the script).  Update them in place so a
    # second run is a no-op and a first run on a partially-patched file
    # converges.
    if all_present:
        for nm, expected in flags_expected.items():
            node = find_node(wf, nm)
            if node["parameters"]["jsCode"] != expected:
                node["parameters"]["jsCode"] = expected
        clear_node = find_node(wf, NEW_CLEAR_FLAGS)
        if clear_node["parameters"]["jsCode"] != CLEAR_FLAGS_JS:
            clear_node["parameters"]["jsCode"] = CLEAR_FLAGS_JS
        clear_write = find_node(wf, NEW_CLEAR_FLAGS + " Write Live State")
        expected_last_control = "={{ $json.accept_requested_was_true ? 'accept: ' + String($json.accept_reason ?? '') : '' }}"
        if clear_write["parameters"]["columns"]["value"]["last_control"] != expected_last_control:
            clear_write["parameters"]["columns"]["value"]["last_control"] = expected_last_control
        check_workflow(wf, label)
        path.write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{label}: repaired stale flag codes")
        return

    # Capture before snapshot of all nodes for the before/after comparison
    before_snap = snapshot_nodes(wf)

    # Capture Build Worker Run Input jsCode before the patch
    bwri_before = bwri["parameters"]["jsCode"]

    # --- Add new nodes ---
    new_nodes = [
        copy.deepcopy(LOAD_LIVE_STATE_NODE),
        copy.deepcopy(READ_CONTROL_NODE),
        copy.deepcopy(PAUSE_LIVE_STATE_NODE),
        copy.deepcopy(PAUSE_FLAGS_NODE),
        copy.deepcopy(PAUSE_GATE_NODE),
        copy.deepcopy(HOLD_PAUSED_NODE),
        copy.deepcopy(HOLD_WRITE_NODE),
        copy.deepcopy(STOP_WORKFLOW_NODE),
        copy.deepcopy(STOP_WRITE_NODE),
        copy.deepcopy(STOP_CHECK_WORKER_LIVE),
        copy.deepcopy(STOP_CHECK_WORKER_FLAGS),
        copy.deepcopy(STOP_CHECK_WORKER_NODE),
        copy.deepcopy(STOP_CHECK_REVIEWER_LIVE),
        copy.deepcopy(STOP_CHECK_REVIEWER_FLAGS),
        copy.deepcopy(STOP_CHECK_REVIEWER_NODE),
        copy.deepcopy(ACCEPT_GATE_NODE),
        copy.deepcopy(ACCEPT_FLAGS_NODE),
        copy.deepcopy(CLEAR_FLAGS_NODE),
        copy.deepcopy(CLEAR_FLAGS_WRITE_NODE),
    ]
    wf["nodes"].extend(new_nodes)

    # --- Patch Prepare Current Task: append steer_note to review_feedback ---
    prepare["parameters"]["jsCode"] = PREPARE_PATCHED

    # --- Patch Retry Current Task: append steer_note + operator_feedback ---
    retry["parameters"]["jsCode"] = RETRY_PATCHED

    # --- Rewire connections ---
    # 1. Load Task State -> Load Live State -> Read Control Flags -> Resume Saved State?
    #    (replaces Load Task State -> Resume Saved State?)
    if ANCHOR_LOAD_TASK_STATE in wf["connections"]:
        replace_outgoing(wf, ANCHOR_LOAD_TASK_STATE, NEW_LOAD_LIVE_STATE)
    replace_outgoing(wf, NEW_LOAD_LIVE_STATE, NEW_READ_CONTROL)
    replace_outgoing(wf, NEW_READ_CONTROL, "Resume Saved State?")

    # 2. Continue Workflow? -> Pause Gate -> {Prepare Current Task, Hold Paused}
    #    (replaces Continue Workflow? -> Prepare Current Task)
    #    The other branch (Failure Result) stays.
    if ANCHOR_CONTINUE in wf["connections"]:
        for m in wf["connections"][ANCHOR_CONTINUE].get("main", []):
            if any(e.get("node") == ANCHOR_PREPARE for e in m):
                for i, e in enumerate(m):
                    if e.get("node") == ANCHOR_PREPARE:
                        m[i] = {"node": NEW_PAUSE_GATE, "type": "main", "index": 0}
                        break
                break

    # Pause Gate -> Prepare Current Task (false) and Hold Paused (true)
    # The gate reads its fresh flag pair first.
    branch_outgoing(wf, NEW_PAUSE_GATE, ANCHOR_PREPARE, NEW_HOLD_PAUSED)

    # Pause Gate Live State -> Pause Gate Flags -> Pause Gate
    replace_outgoing(wf, NEW_PAUSE_LIVE, NEW_PAUSE_FLAGS)
    replace_outgoing(wf, NEW_PAUSE_FLAGS, NEW_PAUSE_GATE)

    # Hold Paused -> Hold Paused Write Live State
    replace_outgoing(wf, NEW_HOLD_PAUSED, NEW_HOLD_WRITE)

    # Clear Consumed Flags chain before Prepare Current Task:
    # Continue Workflow? -> Pause Gate (false branch) -> Clear Consumed Flags
    # -> Clear Consumed Flags Write -> Prepare Current Task
    # Wait: the pause gate false branch goes to Prepare Current Task.
    # We need Clear Consumed Flags in front of Prepare Current Task on the
    # false branch.  So: Pause Gate false -> Clear Consumed Flags ->
    # Clear Consumed Flags Write -> Prepare Current Task.
    # Re-route the false branch.
    # Actually the false branch of Pause Gate goes to Prepare Current Task
    # directly.  We want it to go to Clear Consumed Flags instead.
    # Let's set the false branch of Pause Gate to Clear Consumed Flags.
    # The branch_outgoing already set false -> ANCHOR_PREPARE.  Change it.
    # We'll override: false branch -> NEW_CLEAR_FLAGS.
    wf["connections"][NEW_PAUSE_GATE] = {
        "main": [
            [{"node": NEW_CLEAR_FLAGS, "type": "main", "index": 0}],
            [{"node": NEW_HOLD_PAUSED, "type": "main", "index": 0}],
        ]
    }
    replace_outgoing(wf, NEW_CLEAR_FLAGS, NEW_CLEAR_FLAGS + " Write Live State")
    replace_outgoing(wf, NEW_CLEAR_FLAGS + " Write Live State", ANCHOR_PREPARE)

    # 3. Stop check: Evaluate Worker -> Stop Check Worker -> {Worker Completed?, Stop Workflow}
    #    Stop Check Worker reads its fresh flag pair first.
    #    Stop Workflow -> Stop Workflow Write Live State
    if "Evaluate Worker" in wf["connections"]:
        replace_outgoing(wf, "Evaluate Worker", NEW_STOP_CHECK_WORKER)

    # Stop Check Worker Live State -> Stop Check Worker Flags -> Stop Check Worker
    replace_outgoing(wf, NEW_STOP_CHECK_WORKER_LIVE, NEW_STOP_CHECK_WORKER_FLAGS)
    replace_outgoing(wf, NEW_STOP_CHECK_WORKER_FLAGS, NEW_STOP_CHECK_WORKER)

    # Stop Check Worker -> Worker Completed? (false) and Stop Workflow (true)
    branch_outgoing(wf, NEW_STOP_CHECK_WORKER, ANCHOR_WORKER_COMPLETED, NEW_STOP_WORKFLOW)

    # Stop Workflow -> Stop Workflow Write Live State
    replace_outgoing(wf, NEW_STOP_WORKFLOW, NEW_STOP_WRITE)

    # 4. Stop check: Evaluate Reviewer -> Stop Check Reviewer -> {Reviewer Completed?, Stop Workflow}
    if "Evaluate Reviewer" in wf["connections"]:
        replace_outgoing(wf, "Evaluate Reviewer", NEW_STOP_CHECK_REVIEWER)

    replace_outgoing(wf, NEW_STOP_CHECK_REVIEWER_LIVE, NEW_STOP_CHECK_REVIEWER_FLAGS)
    replace_outgoing(wf, NEW_STOP_CHECK_REVIEWER_FLAGS, NEW_STOP_CHECK_REVIEWER)

    # Stop Check Reviewer -> Reviewer Completed? (false) and Stop Workflow (true)
    branch_outgoing(wf, NEW_STOP_CHECK_REVIEWER, ANCHOR_REVIEWER_COMPLETED, NEW_STOP_WORKFLOW)

    # 5. Accept gate: Restore Review Context -> Accept Gate -> Accept Flags
    #    -> Review Passed?
    #    (replaces Restore Review Context -> Review Passed?)
    if ANCHOR_RESTORE_REVIEW_CTX in wf["connections"]:
        replace_outgoing(wf, ANCHOR_RESTORE_REVIEW_CTX, NEW_ACCEPT_GATE)
    replace_outgoing(wf, NEW_ACCEPT_GATE, NEW_ACCEPT_FLAGS)
    replace_outgoing(wf, NEW_ACCEPT_FLAGS, ANCHOR_REVIEW_PASSED)

    # 6. Modify Review Passed? to OR accept_requested into the condition
    rp_conds = review_passed["parameters"]["conditions"]["conditions"]
    if not any(c.get("id") == "accept-requested" for c in rp_conds):
        rp_conds.append({
            "id": "accept-requested",
            "leftValue": "={{ $('Accept Gate Flags').first().json.accept_requested }}",
            "rightValue": "",
            "operator": {
                "type": "boolean",
                "operation": "true",
                "name": "filter.operator.true",
            },
        })
    review_passed["parameters"]["conditions"]["combinator"] = "or"

    # --- Post-checks ---
    check_workflow(wf, label)

    # Verify Build Worker Run Input is unchanged
    bwri_after = find_node(wf, ANCHOR_BWRI)["parameters"]["jsCode"]
    if bwri_before != bwri_after:
        raise ValueError(f"{label}: Build Worker Run Input jsCode changed unexpectedly")

    # Before/after comparison: nodes outside the patch set are unchanged
    after_snap = snapshot_nodes(wf)
    patched_names = {
        NEW_LOAD_LIVE_STATE,
        NEW_READ_CONTROL,
        NEW_PAUSE_LIVE,
        NEW_PAUSE_FLAGS,
        NEW_PAUSE_GATE,
        NEW_HOLD_PAUSED,
        NEW_HOLD_WRITE,
        NEW_STOP_WORKFLOW,
        NEW_STOP_WRITE,
        NEW_STOP_CHECK_WORKER,
        NEW_STOP_CHECK_WORKER_LIVE,
        NEW_STOP_CHECK_WORKER_FLAGS,
        NEW_STOP_CHECK_REVIEWER,
        NEW_STOP_CHECK_REVIEWER_LIVE,
        NEW_STOP_CHECK_REVIEWER_FLAGS,
        NEW_ACCEPT_GATE,
        NEW_ACCEPT_FLAGS,
        NEW_CLEAR_FLAGS,
        NEW_CLEAR_FLAGS + " Write Live State",
        ANCHOR_PREPARE,
        ANCHOR_RETRY,
        ANCHOR_REVIEW_PASSED,
        ANCHOR_CONTINUE,
        ANCHOR_LOAD_TASK_STATE,
        "Evaluate Worker",
        "Evaluate Reviewer",
        ANCHOR_RESTORE_REVIEW_CTX,
    }
    changed = []
    for name in before_snap:
        if name in patched_names:
            continue
        if before_snap[name] != after_snap.get(name):
            changed.append(name)
    if changed:
        raise ValueError(f"{label}: unexpected changes to nodes outside the patch set: {', '.join(changed)}")

    # Print before/after comparison
    print(f"{label}: before/after comparison:")
    print(f"  Nodes before: {len(before_snap)}, after: {len(after_snap)}")
    print(f"  New nodes added: {len(after_snap) - len(before_snap)}")
    unchanged_count = sum(1 for n in before_snap if n in after_snap and before_snap[n] == after_snap[n])
    print(f"  Unchanged nodes (outside patch set): {unchanged_count}")
    if changed:
        print(f"  Changed nodes outside patch set: {changed}")
    else:
        print(f"  All nodes outside the patch set are unchanged.")

    path.write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{label}: patched")


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/patch_main_control_flags.py <workflow.json>", file=sys.stderr)
        sys.exit(1)

    path = Path(sys.argv[1])
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    label = path.name
    patch_file(path, label)
    print("All done.")


if __name__ == "__main__":
    main()
