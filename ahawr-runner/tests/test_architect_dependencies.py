"""Tests for the Architect dependency block of ``board_dependencies``.

The builder ``build_dependency_context`` reads only the rows its caller hands
it; the ``mission_dependencies`` flag gates the API route, so the flag-off case
is pinned through the route (no dependency fields in the answer) and the empty
``depends_on`` case through the builder itself (it returns "").

The second half of the file covers the two ops patches that put this into the
workflow: ``architect_prompt_dependency_block.py`` (the Architect prompt gets
the block and the split part-order rule) and ``plan_dependency_lint_patch.py``
(the split plan is linted for part order in ``Evaluate Architect`` and a bad
plan returns to the Architect once).  Both patches are applied to a temporary
copy of the launch export (``data-tables/ops/wf_next.json``) or, for the
anchor checks, to a fixture that carries only the anchored node; nothing here
writes to the export file.  When the export copy is not in the environment the
export-backed tests skip with the file named and the fixture tests still run.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import ahawr_runner.board_dependencies as BD
from ahawr_runner import board
from ahawr_runner.config import Settings
from tests.test_mission_dependencies import (
    FLAG_OFF_JSON,
    PINNING_STAMP,
    DependencyStore,
    client_for_store,
)
from tests.test_ops_patches_qa import _sha256


def test_architect_dependencies_block_has_heading_ids_names_states_and_rule() -> None:
    mission = {"mission_id": "m3", "depends_on": ["m1", "m2"]}
    rows = [
        {
            "mission_id": "m1",
            "name": "Source",
            "report": "report of m1",
            "summary": "summary of m1",
        },
        {"mission_id": "m2", "name": "Helper", "report": "", "summary": "summary of m2"},
    ]
    states = {"m1": "completed", "m2": "active"}
    block = BD.build_dependency_context(mission, rows, states)

    assert block.startswith(BD.DEPENDENCY_BLOCK_HEADING)
    assert '- m1 "Source" [completed]: report of m1' in block
    assert '- m2 "Helper" [active]: summary of m2' in block
    assert f"Rule: {BD.ARCHITECT_DEPENDENCY_RULE}" in block


def test_architect_dependencies_prefer_final_report_over_plan_summary() -> None:
    block = BD.build_dependency_context(
        {"mission_id": "m3", "depends_on": ["m1"]},
        [
            {
                "mission_id": "m1",
                "name": "Source",
                "report": "the final report wins",
                "summary": "the plan summary is ignored",
            }
        ],
        {"m1": "completed"},
    )
    assert "the final report wins" in block
    assert "the plan summary is ignored" not in block


def test_architect_dependencies_use_plan_summary_when_no_report_exists() -> None:
    block = BD.build_dependency_context(
        {"mission_id": "m3", "depends_on": ["m1", "m2"]},
        [
            {"mission_id": "m1", "name": "Source", "report": "", "summary": "summary of m1"},
            {"mission_id": "m2", "name": "Helper", "report": "", "summary": ""},
        ],
        {"m1": "active", "m2": "queued"},
    )
    assert '- m1 "Source" [active]: summary of m1' in block
    # a dependency with neither a report nor a summary still gets a line
    assert f'- m2 "Helper" [queued]: {BD.DEPENDENCY_TEXT_FALLBACK}' in block


def test_architect_dependencies_cut_each_dependency_text_to_the_limit_marked() -> None:
    long_report = "word " * 400  # 2000 characters, over DEPENDENCY_REPORT_MAX_CHARS
    block = BD.build_dependency_context(
        {"mission_id": "m3", "depends_on": ["m1"]},
        [{"mission_id": "m1", "name": "Source", "report": long_report, "summary": ""}],
        {"m1": "completed"},
    )
    detail = block.splitlines()[1].split("]: ", 1)[1]
    assert len(detail) == BD.DEPENDENCY_REPORT_MAX_CHARS
    assert detail.endswith(BD.TRUNCATION_MARK)
    kept = long_report[: BD.DEPENDENCY_REPORT_MAX_CHARS - len(BD.TRUNCATION_MARK)]
    assert detail == kept + BD.TRUNCATION_MARK


def test_architect_dependencies_cut_the_whole_block_to_the_limit_marked() -> None:
    mission = {"mission_id": "m99", "depends_on": [f"m{i}" for i in range(40)]}
    rows = [
        {"mission_id": f"m{i}", "name": f"Mission {i}", "report": "x" * 200, "summary": ""}
        for i in range(40)
    ]
    states = {f"m{i}": "completed" for i in range(40)}
    block = BD.build_dependency_context(mission, rows, states)
    assert len(block) <= BD.ARCHITECT_BLOCK_MAX_CHARS
    assert block.endswith(BD.TRUNCATION_MARK)


def test_architect_dependencies_return_empty_string_for_empty_depends_on() -> None:
    assert BD.build_dependency_context({"mission_id": "m1", "depends_on": []}, [], {}) == ""
    assert BD.build_dependency_context({"mission_id": "m1", "depends_on": None}, [], {}) == ""
    assert BD.build_dependency_context({"depends_on": ["  ", ""]}, [{"mission_id": "m1"}], {}) == ""


def test_architect_dependencies_exact_block_text_for_a_small_fixture() -> None:
    block = BD.build_dependency_context(
        {"mission_id": "m3", "depends_on": ["m1"]},
        [
            {
                "mission_id": "m1",
                "name": "Source",
                "report": "built the ledger",
                "summary": "older plan summary",
            }
        ],
        {"m1": "completed"},
    )
    expected = (
        "Missions this one depends on\n"
        '- m1 "Source" [completed]: built the ledger\n'
        "Rule: the work of those missions exists, do not plan it again; "
        "do not write tasks that need what a dependency has not delivered; "
        "when something a task needs is missing in a dependency, say so in the plan notes"
    )
    assert block == expected


@pytest.fixture
def flag_off_dependency_store() -> DependencyStore:
    """One dependency wired (m2 waits on m1) with ``mission_dependencies`` off."""
    store = DependencyStore()
    store._missions["m1"] = board.Mission(
        mission_id="m1",
        state_namespace="sk1",
        name="Source",
        working_directory="/tmp/x",
        updated_at=PINNING_STAMP,
    )
    store._missions["m2"] = board.Mission(
        mission_id="m2",
        state_namespace="sk2",
        name="Helper",
        working_directory="/tmp/y",
        updated_at=PINNING_STAMP,
    )
    store._queue["m1"] = board.QueueEntry(position=0, mission_id="m1", enabled=True)
    store._queue["m2"] = board.QueueEntry(position=1, mission_id="m2", enabled=True)
    store._queue_order.extend(["m1", "m2"])
    store.set_dependencies("m2", ["m1"], None)
    store.update_hermes_config(None, features_json=FLAG_OFF_JSON)
    return store


async def test_architect_dependencies_flag_off_route_has_no_dependency_block(
    settings: Settings, flag_off_dependency_store: DependencyStore
) -> None:
    """The flag off: even a wired dependency adds no block to the mission answer."""
    async for client in client_for_store(settings, flag_off_dependency_store):
        answer = await client.get("/api/board/missions/m2")
        assert answer.status_code == 200
        body = answer.json()
        # the dependency fields the flag would add are absent: the block is empty
        assert body.get("dependency_context", "") == ""
        assert body.get("dependency_state", "") == ""


# --- the ops patches of T034 and T035: prompt block, split rule, plan lint ---

OPS_DIR = Path(__file__).resolve().parents[2] / "data-tables" / "ops"
#: The export launch_mission.sh patches: read here, never written.
EXPORT_COPY = OPS_DIR / "wf_next.json"
ARCHITECT_PATCH_PATH = OPS_DIR / "architect_prompt_dependency_block.py"
LINT_PATCH_PATH = OPS_DIR / "plan_dependency_lint_patch.py"
ARCHITECT_NODE = "Build Architect Run Input"
EVALUATE_NODE = "Evaluate Architect"
#: The node that loops a gate retry back to the Architect, added by the lint patch.
PLAN_RETRY_ROUTER = "Plan Retry Router"
MISSION_ID = "mission-architect-test-01"
RUNNER_URL = "http://127.0.0.1:8080"
#: The node code of the Architect runs one fetch and one plan pass: 30 seconds is
#: more than a stubbed fetch needs, the same bound the other node tests use.
RUNNER_TIMEOUT_SECONDS = 30

needs_export = pytest.mark.skipif(
    not EXPORT_COPY.is_file(),
    reason=f"live export copy is not available: {EXPORT_COPY}",
)
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _load_patch_module(name: str, path: Path) -> ModuleType:
    """Import an ops patch; a missing file is a failure, not a skip.

    The patch file is the change under test, so without it these tests must not
    silently pass: the module raises and pytest reports a collection error.
    """
    if not path.is_file():
        raise ImportError(f"the ops patch under test is missing: {path}")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ARCHITECT_PATCH = _load_patch_module("architect_prompt_dependency_block", ARCHITECT_PATCH_PATH)
LINT_PATCH = _load_patch_module("plan_dependency_lint_patch", LINT_PATCH_PATH)

# the Architect node of the export reads only these three nodes and one board answer
RUNNER_JS = """
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const $ = (name) => {
  if (!(name in input.nodes)) throw new Error('no stub for node ' + name);
  const items = input.nodes[name];
  return { isExecuted: true, first: () => ({ json: items }), last: () => ({ json: items }) };
};
const helpers = {
  httpRequest: async () => {
    if (input.fetch_error) throw new Error('the board answer is unavailable');
    return input.board_answer;
  },
};
new AsyncFunction('$json', '$', input.code)
  .call({ helpers }, input.json, $)
  .then((out) => process.stdout.write(JSON.stringify(out)))
  .catch((err) => { process.stderr.write(String(err.stack ?? err)); process.exit(1); });
"""

ARCHITECT_PROMPT = "Plan the mission as JSON."
ARCHITECT_CONSTANTS: dict[str, Any] = {
    "roles_json": json.dumps(
        {
            "architect": {"1": "alpha-provider/model-architect"},
            "worker": {"1": "beta-provider/model-worker"},
            "reviewer": {"1": "gamma-provider/model-reviewer"},
        }
    ),
    "max_tasks": 15,
    "plan_no_truncate": 1,
    "report_pack": 0,
    "architect_poll_seconds": 3,
    "architect_max_polls": 30,
    "architect_max_provider_retries": 5,
    "retry_delays": [15, 30],
    "state_namespace": "ns-architect-test",
    "service_provider": "service-provider",
    "service_model": "service-provider/model-service",
    "runner_url": RUNNER_URL,
}
ARCHITECT_MISSION: dict[str, Any] = {
    "mission_id": MISSION_ID,
    "original_goal": "Add the ledger store to the dashboard.",
    "working_directory_block": "",
    "working_directory": "/tmp/ahawr-test",
}
#: The plan gate splits a 4-task plan with max_tasks 2 into parts [t1, t2] and [t3, t4].
EVALUATE_CONSTANTS: dict[str, Any] = {
    "plan_no_truncate": 1,
    "max_tasks": 2,
    "max_tasks_hard": 30,
    "architect_max_provider_retries": 3,
}
DEPENDENCY_BLOCK = BD.build_dependency_context(
    {"mission_id": MISSION_ID, "depends_on": ["m-src"]},
    [
        {
            "mission_id": "m-src",
            "name": "Source",
            "report": "built the ledger module",
            "summary": "older plan summary",
        }
    ],
    {"m-src": "completed"},
)


def _export_workflow() -> dict[str, Any]:
    """A fresh in-memory copy of the launch export, never written back."""
    raw = json.loads(EXPORT_COPY.read_text(encoding="utf-8"))
    workflow: dict[str, Any] = raw[0] if isinstance(raw, list) else raw
    return workflow


def _node_code(workflow: dict[str, Any], name: str) -> str:
    code: str = next(
        node["parameters"]["jsCode"] for node in workflow["nodes"] if node.get("name") == name
    )
    return code


def _architect_anchor_fixture() -> dict[str, Any]:
    """A workflow holding only the Architect node, with the three patch anchors."""
    return {
        "nodes": [
            {
                "name": ARCHITECT_NODE,
                "parameters": {
                    "jsCode": "\n".join(
                        [
                            ARCHITECT_PATCH.ANCHOR_FEEDBACK,
                            ARCHITECT_PATCH.ANCHOR_BLOCK,
                            ARCHITECT_PATCH.ANCHOR_ORDER,
                        ]
                    )
                },
            }
        ]
    }


def _run_node(code: str, fixture: dict[str, Any]) -> dict[str, Any]:
    """Run one node jsCode in node with stubbed `$()` and `helpers.httpRequest`."""
    done = subprocess.run(
        ["node", "-e", RUNNER_JS],
        input=json.dumps({"code": code, **fixture}),
        capture_output=True,
        text=True,
        timeout=RUNNER_TIMEOUT_SECONDS,
    )
    if done.returncode != 0:
        raise AssertionError(f"the node code failed: {done.stderr[:400]}")
    out = json.loads(done.stdout)
    # n8n Code nodes return {json: ...} or an item array; the tests read the item fields
    if isinstance(out, list):
        out = out[0]
    body = out.get("json", out) if isinstance(out, dict) else out
    assert isinstance(body, dict)
    return body


def _architect_prompt_case(
    board_answer: dict[str, Any] | None,
    fetch_error: bool,
    split_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "json": {"gate_feedback": "", "split_request": split_request},
        "nodes": {
            "Constants": ARCHITECT_CONSTANTS,
            "Mission": ARCHITECT_MISSION,
            "Prompt Templates": {"architect_prompt": ARCHITECT_PROMPT},
        },
        "board_answer": board_answer,
        "fetch_error": fetch_error,
    }


def _architect_codes() -> tuple[str, str]:
    """The Architect node code before and after the dependency-block patch."""
    workflow = _export_workflow()
    unpatched = _node_code(workflow, ARCHITECT_NODE)
    assert ARCHITECT_PATCH.patch(workflow) is True
    return unpatched, _node_code(workflow, ARCHITECT_NODE)


def _fully_patched_architect_code() -> str:
    """The Architect node code after both patches, the one that carries the flag."""
    workflow = _export_workflow()
    assert ARCHITECT_PATCH.patch(workflow) is True
    LINT_PATCH.patch(workflow)
    return _node_code(workflow, ARCHITECT_NODE)


def _evaluate_codes() -> tuple[str, str]:
    """Evaluate Architect as it ran before the lint patch and as it runs after both."""
    workflow = _export_workflow()
    unpatched = _node_code(workflow, EVALUATE_NODE)
    ARCHITECT_PATCH.patch(workflow)
    LINT_PATCH.patch(workflow)
    return unpatched, _node_code(workflow, EVALUATE_NODE)


def _plan_case(
    flag_on: bool,
    plan: str,
    architect_input: str = "",
    gate_retry_count: int = 0,
) -> dict[str, Any]:
    """The Evaluate Architect input of one pass, with the flag the Architect carried out."""
    return {
        "json": {
            "status": "done",
            "route": "ok",
            "output": plan,
            "gate_feedback": "",
            "gate_retry_count": gate_retry_count,
            "poll_count": 1,
        },
        "nodes": {
            "Constants": EVALUATE_CONSTANTS,
            "Build Architect Run Input": {
                "dependency_flag_on": flag_on,
                "input": architect_input,
            },
        },
    }


def _plan_task(
    task_id: str,
    objective: str,
    scope: list[str],
    dependencies: list[str],
    phase: int,
    produced: list[str] | None = None,
) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": task_id,
        "objective": objective,
        "scope": scope,
        "dependencies": dependencies,
        "phase": phase,
    }
    if produced is not None:
        task["produced"] = produced
    return task


def _plan(tasks: list[dict[str, Any]]) -> str:
    return json.dumps({"summary": "the test plan", "tasks": tasks})


def _sound_plan() -> list[dict[str, Any]]:
    """Part 2 uses only what part 1 and its own earlier tasks give it."""
    return [
        _plan_task("t1", "wire the ledger store", ["ledger.py"], [], 1),
        _plan_task("t2", "test the ledger store", ["test_ledger.py"], ["t1"], 1),
        _plan_task("t3", "expose the ledger over http", ["api.py"], ["t2"], 2),
        _plan_task("t4", "record the ledger rows", ["store.py"], ["t3"], 2, ["rows.json"]),
    ]


def _later_part_file_plan() -> list[dict[str, Any]]:
    """t1 of part 1 scopes ledger.py, which only part 2 produces."""
    tasks = _sound_plan()
    tasks[3] = _plan_task("t4", "record the ledger rows", ["store.py"], ["t3"], 2, ["ledger.py"])
    return tasks


def _later_part_task_plan() -> list[dict[str, Any]]:
    """t1 of part 1 declares t4 of part 2 as its dependency."""
    tasks = _sound_plan()
    tasks[0] = _plan_task("t1", "wire the ledger store", ["ledger.py"], ["t4"], 1)
    return tasks


def _python_problems(plan_parts: list[list[str]], tasks: list[dict[str, Any]]) -> list[str]:
    tasks_by_id = {task["id"]: task for task in tasks}
    by_part = {
        number: [tasks_by_id[task_id] for task_id in part]
        for number, part in enumerate(plan_parts, start=1)
    }
    problems: list[str] = BD.find_part_order_problems(plan_parts, by_part)
    return problems


@needs_export
def test_architect_dependencies_prompt_patch_applied_twice_leaves_the_same_bytes(
    tmp_path: Path,
) -> None:
    """The CLI run twice on a copy of the export: the second run changes nothing."""
    source_sha = _sha256(EXPORT_COPY)
    copy = tmp_path / "wf_copy.json"
    shutil.copyfile(EXPORT_COPY, copy)
    first = subprocess.run(
        [sys.executable, str(ARCHITECT_PATCH_PATH), str(copy), str(copy)],
        capture_output=True,
        text=True,
        check=True,
        timeout=RUNNER_TIMEOUT_SECONDS,
    )
    assert "architect dependency block: patched" in first.stdout
    bytes_after_first = copy.read_bytes()
    second = subprocess.run(
        [sys.executable, str(ARCHITECT_PATCH_PATH), str(copy), str(copy)],
        capture_output=True,
        text=True,
        check=True,
        timeout=RUNNER_TIMEOUT_SECONDS,
    )
    assert "architect dependency block: already patched" in second.stdout
    assert copy.read_bytes() == bytes_after_first
    assert _sha256(EXPORT_COPY) == source_sha


@needs_export
def test_architect_dependencies_prompt_patch_never_writes_the_export_it_reads(
    tmp_path: Path,
) -> None:
    """The patch edits an in-memory copy of the export and leaves the file itself alone."""
    source_sha = _sha256(EXPORT_COPY)
    workflow = _export_workflow()
    assert ARCHITECT_PATCH.patch(workflow) is True
    assert LINT_PATCH.patch(workflow)
    assert PLAN_RETRY_ROUTER in [node.get("name") for node in workflow["nodes"]]
    assert _sha256(EXPORT_COPY) == source_sha


def test_architect_dependencies_prompt_patch_second_run_changes_nothing() -> None:
    workflow = _architect_anchor_fixture()
    assert ARCHITECT_PATCH.patch(workflow) is True
    code = workflow["nodes"][0]["parameters"]["jsCode"]
    assert code.count(ARCHITECT_PATCH.MARKER) == 1
    assert ARCHITECT_PATCH.patch(workflow) is False
    assert workflow["nodes"][0]["parameters"]["jsCode"] == code


def test_architect_dependencies_prompt_patch_refuses_a_missing_anchor_naming_it() -> None:
    """One anchor line removed exits with the count 0 and that anchor named."""
    workflow = _architect_anchor_fixture()
    workflow["nodes"][0]["parameters"]["jsCode"] = "\n".join(
        line
        for line in workflow["nodes"][0]["parameters"]["jsCode"].splitlines()
        if line != ARCHITECT_PATCH.ANCHOR_BLOCK
    )
    with pytest.raises(SystemExit) as caught:
        ARCHITECT_PATCH.patch(workflow)
    message = str(caught.value)
    assert "anchor occurs 0 times, expected 1" in message
    assert ARCHITECT_NODE in message
    assert ARCHITECT_PATCH.ANCHOR_BLOCK[:40] in message


def test_architect_dependencies_prompt_patch_refuses_a_doubled_anchor_with_the_count() -> None:
    workflow = _architect_anchor_fixture()
    duplicated = ARCHITECT_PATCH.ANCHOR_ORDER
    workflow["nodes"][0]["parameters"]["jsCode"] = "\n".join(
        [duplicated, duplicated, ARCHITECT_PATCH.ANCHOR_BLOCK, ARCHITECT_PATCH.ANCHOR_FEEDBACK]
    )
    with pytest.raises(SystemExit) as caught:
        ARCHITECT_PATCH.patch(workflow)
    message = str(caught.value)
    assert "anchor occurs 2 times, expected 1" in message
    assert "List the tasks in the order" in message


def test_architect_dependencies_prompt_patch_refuses_a_missing_or_duplicated_node() -> None:
    with pytest.raises(SystemExit) as caught:
        ARCHITECT_PATCH.patch({"nodes": []})
    assert "anchor node must occur exactly once" in str(caught.value)
    assert "found 0" in str(caught.value)

    workflow = _architect_anchor_fixture()
    workflow["nodes"].append(dict(workflow["nodes"][0]))
    with pytest.raises(SystemExit) as caught:
        ARCHITECT_PATCH.patch(workflow)
    assert "found 2" in str(caught.value)


def test_architect_dependencies_split_rule_text_is_in_the_patched_architect_node() -> None:
    """The part-order rule of section (g) is written into the patched node code."""
    workflow = _architect_anchor_fixture()
    assert ARCHITECT_PATCH.patch(workflow) is True
    code = workflow["nodes"][0]["parameters"]["jsCode"]
    assert "PART ORDER OF THE SPLIT:" in code
    assert "depend only on an earlier part" in code
    assert "produced only by a later part" in code
    assert "const DEPENDENCY_FETCH_TIMEOUT_MS = 30000;" in code
    assert code.count("/api/board/missions/") == 1
    assert code.count(ARCHITECT_PATCH.MARKER) == 1


@needs_export
@needs_node
def test_architect_dependencies_flag_off_prompt_is_byte_for_byte_the_unpatched_prompt() -> None:
    """With the flag off the board answer has no `depends_on`: the prompt is unchanged."""
    unpatched_code, patched_code = _architect_codes()
    assert ARCHITECT_PATCH.MARKER in patched_code
    flag_off_answer = {"mission_id": MISSION_ID, "name": "Test mission", "state": "active"}
    unpatched = _run_node(unpatched_code, _architect_prompt_case(flag_off_answer, False))
    patched = _run_node(patched_code, _architect_prompt_case(flag_off_answer, False))
    assert patched["input"] == unpatched["input"]
    assert patched == unpatched
    assert "Missions this one depends on" not in patched["input"]
    assert "PART ORDER OF THE SPLIT:" not in patched["input"]


@needs_export
@needs_node
def test_architect_dependencies_flag_on_plan_prompt_carries_the_block_and_the_rule() -> None:
    """The only change to the plan prompt is the dependency block, in its exact place."""
    unpatched_code, patched_code = _architect_codes()
    board_answer = {
        "mission_id": MISSION_ID,
        "depends_on": ["m-src"],
        "dependency_context": DEPENDENCY_BLOCK,
    }
    unpatched = _run_node(unpatched_code, _architect_prompt_case(board_answer, False))
    patched = _run_node(patched_code, _architect_prompt_case(board_answer, False))
    assert DEPENDENCY_BLOCK in patched["input"]
    assert BD.DEPENDENCY_BLOCK_HEADING in patched["input"]
    assert f"Rule: {BD.ARCHITECT_DEPENDENCY_RULE}" in patched["input"]
    # the block sits right after the mission text: everywhere else is byte for byte the same
    head, tail = unpatched["input"].split("END MISSION", 1)
    assert patched["input"] == head + "END MISSION" + "\n\n" + DEPENDENCY_BLOCK + tail
    assert patched["role"] == unpatched["role"]


@needs_export
@needs_node
def test_architect_dependencies_flag_on_split_prompt_carries_the_part_order_rule() -> None:
    """The split request prompt gains the part-order rule only with the flag on."""
    unpatched_code, patched_code = _architect_codes()
    split_request = {
        "task": {"id": "t1", "title": "wire the ledger", "objective": "wire the ledger store"},
        "digest": {"done": ["t1a"]},
    }
    board_answer = {"mission_id": MISSION_ID, "depends_on": [], "dependency_context": ""}
    unpatched = _run_node(
        unpatched_code, _architect_prompt_case(board_answer, False, split_request)
    )
    patched = _run_node(
        patched_code, _architect_prompt_case(board_answer, False, split_request)
    )
    assert "PART ORDER OF THE SPLIT:" in patched["input"]
    assert "List the tasks in the order they must be done" in patched["input"]
    assert "PART ORDER OF THE SPLIT:" not in unpatched["input"]
    flag_off = _run_node(
        patched_code,
        _architect_prompt_case({"mission_id": MISSION_ID}, False, split_request),
    )
    assert flag_off["input"] == unpatched["input"]


@needs_export
@needs_node
def test_architect_dependencies_board_fetch_failure_leaves_the_prompt_unchanged() -> None:
    """The fetch is bounded (one request, a timeout) and its failure adds nothing."""
    unpatched_code, patched_code = _architect_codes()
    unpatched = _run_node(unpatched_code, _architect_prompt_case(None, True))
    patched = _run_node(patched_code, _architect_prompt_case(None, True))
    assert patched == unpatched
    assert "Missions this one depends on" not in patched["input"]
    # the fetch is bounded: exactly one request, with the timeout in its own named constant
    assert patched_code.count("this.helpers.httpRequest") == 1
    assert "timeout: DEPENDENCY_FETCH_TIMEOUT_MS" in patched_code
    assert "const DEPENDENCY_FETCH_TIMEOUT_MS = 30000;" in patched_code


@needs_export
@needs_node
def test_architect_dependencies_flag_on_with_no_dependencies_adds_no_block() -> None:
    """The flag is on but `depends_on` is empty: no block, the prompt is unchanged."""
    unpatched_code, patched_code = _architect_codes()
    board_answer = {"mission_id": MISSION_ID, "depends_on": [], "dependency_context": ""}
    unpatched = _run_node(unpatched_code, _architect_prompt_case(board_answer, False))
    patched = _run_node(patched_code, _architect_prompt_case(board_answer, False))
    assert patched["input"] == unpatched["input"]
    assert "Missions this one depends on" not in patched["input"]
    # the flag is on for the lint of Evaluate Architect even though no block was added
    flag_case = _architect_prompt_case(board_answer, False)
    both_patched = _run_node(_fully_patched_architect_code(), flag_case)
    assert both_patched["dependency_flag_on"] is True
    assert both_patched["input"] == unpatched["input"]


@needs_export
@needs_node
def test_architect_dependencies_lint_accepts_a_plan_where_every_part_is_sound() -> None:
    """A plan whose parts use only their own or an earlier part's output passes the lint."""
    _, patched_code = _evaluate_codes()
    out = _run_node(patched_code, _plan_case(True, _plan(_sound_plan())))
    assert out["plan_parts"] == [["t1", "t2"], ["t3", "t4"]]
    assert "architect_gate_retry" not in out
    assert "[Plan lint]" not in out["architect_output"]
    assert "plan lint" not in out["plan_warnings"]


@needs_export
@needs_node
def test_architect_dependencies_lint_rejects_a_scope_file_from_a_later_part() -> None:
    """t1 of part 1 scopes a file only part 2 produces: the plan returns to the Architect."""
    _, patched_code = _evaluate_codes()
    out = _run_node(patched_code, _plan_case(True, _plan(_later_part_file_plan())))
    assert out["architect_gate_retry"] is True
    assert out["gate_retry_count"] == 1
    assert out["gate_feedback"].startswith("PART ORDER LINT:")
    assert "task t1 of part 1 names ledger.py, which is produced only in part 2" in out[
        "gate_feedback"
    ]


@needs_export
@needs_node
def test_architect_dependencies_lint_rejects_a_task_id_named_by_an_earlier_part() -> None:
    """t1 of part 1 depends on t4 of part 2: the lint names the task and both parts."""
    _, patched_code = _evaluate_codes()
    out = _run_node(patched_code, _plan_case(True, _plan(_later_part_task_plan())))
    assert out["architect_gate_retry"] is True
    assert "task t1 of part 1 names t4, which is produced only in part 2" in out["gate_feedback"]
    assert "Every part may use only its own earlier tasks" in out["gate_feedback"]


@needs_export
@needs_node
def test_architect_dependencies_lint_is_skipped_when_the_flag_is_off() -> None:
    """With the flag off the same bad plan takes the plan path of before the patch."""
    unpatched_code, patched_code = _evaluate_codes()
    case = _plan_case(False, _plan(_later_part_task_plan()))
    unpatched = _run_node(unpatched_code, case)
    patched = _run_node(patched_code, case)
    assert patched == unpatched
    assert "architect_gate_retry" not in patched
    assert "[Plan lint]" not in patched["architect_output"]
    assert "const GATE_RETRY_ROUTED = dependencyFlagOn;" in patched_code


@needs_export
@needs_node
def test_architect_dependencies_lint_return_to_the_architect_is_bounded_to_one() -> None:
    """After the one retry the plan is accepted with a note, never retried again."""
    unpatched_code, patched_code = _evaluate_codes()
    assert "const LINT_RETRY_LIMIT = 1;" in patched_code
    bad_plan = _plan(_later_part_task_plan())
    already_retried = _run_node(
        patched_code,
        _plan_case(True, bad_plan, architect_input="... PART ORDER LINT: ...", gate_retry_count=0),
    )
    lint_note = "plan lint: 1 part-order problems after the one retry"
    assert "architect_gate_retry" not in already_retried
    assert lint_note in already_retried["plan_warnings"]
    assert f"[Plan lint] {lint_note}; the plan is accepted" in already_retried["architect_output"]
    counted_once = _run_node(patched_code, _plan_case(True, bad_plan, gate_retry_count=1))
    assert "architect_gate_retry" not in counted_once
    # the node of before the patch never routes a plan back, however the retry is counted
    before_lint = _run_node(unpatched_code, _plan_case(True, bad_plan, gate_retry_count=1))
    assert "architect_gate_retry" not in before_lint
    assert "plan lint" not in before_lint["plan_warnings"]


@needs_export
@needs_node
def test_architect_dependencies_lint_js_messages_match_the_python_rule() -> None:
    """The JS mirror in the node emits the same problems as find_part_order_problems."""
    _, patched_code = _evaluate_codes()
    tasks = _later_part_task_plan()
    plan = _plan(tasks)
    parts_run = _run_node(patched_code, _plan_case(False, plan))
    plan_parts = [list(part) for part in parts_run["plan_parts"]]
    python_problems = _python_problems(plan_parts, tasks)
    assert python_problems == [
        "task t1 of part 1 names t4, which is produced only in part 2"
    ]
    retry_run = _run_node(patched_code, _plan_case(True, plan))
    js_problems = retry_run["gate_feedback"].split("Fix the plan:\n", 1)[1].splitlines()
    assert js_problems == ["- " + problem for problem in python_problems]


@needs_export
@needs_node
def test_architect_dependencies_flag_is_carried_out_of_the_patched_architect_node() -> None:
    """The patched Architect node carries the flag the lint of Evaluate Architect reads."""
    workflow = _export_workflow()
    ARCHITECT_PATCH.patch(workflow)
    LINT_PATCH.patch(workflow)
    code = _node_code(workflow, ARCHITECT_NODE)
    flag_on = _run_node(
        code, _architect_prompt_case({"mission_id": MISSION_ID, "depends_on": ["m-src"]}, False)
    )
    flag_off = _run_node(code, _architect_prompt_case({"mission_id": MISSION_ID}, False))
    fetch_failed = _run_node(code, _architect_prompt_case(None, True))
    assert flag_on["dependency_flag_on"] is True
    assert flag_off["dependency_flag_on"] is False
    assert fetch_failed["dependency_flag_on"] is False
    carried_flag = 'dependency_flag_on: (typeof dependencyFlagOn === "boolean" ? dependencyFlagOn'
    assert carried_flag in code
    assert ARCHITECT_PATCH.MARKER in code
    assert LINT_PATCH.MARKER in code
