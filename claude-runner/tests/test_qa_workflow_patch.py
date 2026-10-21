from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TESTS_DIR = Path(__file__).resolve().parent
PATCH_PATH = Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "qa_workflow_patch.py"
PLANNING_PROMPT = """You are the lead architect.

LANGUAGE:
- Write in English.

Return ONLY valid JSON.

The top-level JSON object MUST match this schema:

{"summary": "...", "tasks": []}

BEGIN MISSION
{{MISSION}}
END MISSION
"""
FINAL_PROMPT = """You are the reviewer of one task.

BEGIN MISSION
{{MISSION}}
END MISSION

QA_VERDICT_ANCHOR
"""


@pytest.fixture(scope="module")
def qwp() -> ModuleType:
    spec = importlib.util.spec_from_file_location("qa_workflow_patch", PATCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _insert_point(text: str, anchor: str) -> tuple[str, str]:
    head, tail = text.split(anchor, 1)
    return head, anchor + tail


def test_mode_off_renders_the_prompt_byte_for_byte(qwp: ModuleType) -> None:
    planning = qwp.render_planning_prompt(PLANNING_PROMPT, "off")
    final = qwp.render_final_prompt(FINAL_PROMPT, "off")
    assert planning.encode("utf-8") == PLANNING_PROMPT.encode("utf-8")
    assert final.encode("utf-8") == FINAL_PROMPT.encode("utf-8")


def test_mode_off_returns_a_final_prompt_without_the_marker_unchanged(qwp: ModuleType) -> None:
    text = "You are the reviewer. No marker here.\n"
    assert qwp.render_final_prompt(text, "off") == text


@pytest.mark.parametrize("mode", ["final", "ui_tasks", "nightly"])
def test_mode_on_inserts_both_blocks_before_their_anchors(qwp: ModuleType, mode: str) -> None:
    planning = qwp.render_planning_prompt(PLANNING_PROMPT, mode)
    head, tail = _insert_point(PLANNING_PROMPT, qwp.PLANNING_ANCHOR)
    assert planning.startswith(head)
    assert planning.endswith(tail)
    block = planning[len(head) : len(planning) - len(tail)]
    assert block.startswith(qwp.PLANNING_MARK)
    assert block.endswith("\n\n")
    assert "qa = {" in block and "ui_check = {" in block
    assert "at most three per mission" in block

    final = qwp.render_final_prompt(FINAL_PROMPT, mode)
    head, tail = _insert_point(FINAL_PROMPT, qwp.FINAL_ANCHOR)
    assert final.startswith(head)
    assert final.endswith(tail)
    verdict = final[len(head) : len(final) - len(tail)]
    assert verdict.startswith(qwp.FINAL_MARK)
    assert "Blocked is never a pass" in verdict


@pytest.mark.parametrize("mode", ["final", "ui_tasks"])
def test_second_apply_changes_nothing(qwp: ModuleType, mode: str) -> None:
    planning_once = qwp.render_planning_prompt(PLANNING_PROMPT, mode)
    assert qwp.render_planning_prompt(planning_once, mode) == planning_once
    final_once = qwp.render_final_prompt(FINAL_PROMPT, mode)
    assert qwp.render_final_prompt(final_once, mode) == final_once


def test_missing_anchor_aborts(qwp: ModuleType) -> None:
    text = PLANNING_PROMPT.replace("Return ONLY valid JSON.", "Return JSON.")
    with pytest.raises(qwp.AnchorError, match="exactly once in the prompt, found 0"):
        qwp.render_planning_prompt(text, "final")


def test_duplicate_anchor_aborts(qwp: ModuleType) -> None:
    text = FINAL_PROMPT + "\nQA_VERDICT_ANCHOR\n"
    with pytest.raises(qwp.AnchorError, match="exactly once in the prompt, found 2"):
        qwp.render_final_prompt(text, "final")


@pytest.mark.parametrize("mode", ["final", "ui_tasks"])
def test_final_prompt_without_the_marker_is_not_changed(qwp: ModuleType, mode: str) -> None:
    text = "You are the reviewer. No marker here.\n"
    with pytest.raises(qwp.AnchorError, match="exactly once in the prompt, found 0"):
        qwp.render_final_prompt(text, mode)


def test_unknown_mode_is_rejected(qwp: ModuleType) -> None:
    with pytest.raises(ValueError, match="qa_mode 'sometimes'"):
        qwp.render_planning_prompt(PLANNING_PROMPT, "sometimes")


def test_crlf_prompt_keeps_crlf_line_endings(qwp: ModuleType) -> None:
    crlf = PLANNING_PROMPT.replace("\n", "\r\n")
    planning = qwp.render_planning_prompt(crlf, "final")
    assert planning.count("\n") == planning.count("\r\n")
    assert qwp.render_planning_prompt(planning, "final") == planning


def test_ui_check_cases_have_12_cases_split_6_and_6() -> None:
    cases = json.loads((TESTS_DIR / "qa_ui_check_cases.json").read_text(encoding="utf-8"))
    assert len(cases) == 12
    assert len({case["id"] for case in cases}) == 12
    for case in cases:
        assert set(case) == {"id", "objective", "needed", "why"}
        assert isinstance(case["objective"], str) and case["objective"].strip()
        assert isinstance(case["needed"], bool)
        assert isinstance(case["why"], str) and case["why"].strip() and "\n" not in case["why"]
    assert sum(case["needed"] for case in cases) == 6


def _link(name: str) -> dict[str, Any]:
    return {"node": name, "type": "main", "index": 0}


def _workflow() -> dict[str, Any]:
    def node(name: str) -> dict[str, Any]:
        kind = "n8n-nodes-base.noOp"
        return {"name": name, "type": kind, "position": [100, 200], "parameters": {}}

    names = ["All Tasks Completed?", "Parse Review", "Parse Plan", "Approved Result", "Retry"]
    return {
        "nodes": [node(name) for name in names],
        "connections": {
            "All Tasks Completed?": {
                "main": [[_link("Approved Result")], [_link("Retry")]],
            },
        },
    }


def _run_js(
    code: str, json_in: dict[str, Any], tasks: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    script = (
        "const $ = () => ({ first: () => ({ json: { tasks: "
        + json.dumps(tasks or []) + " } }) });\n"
        "const out = new Function('$json', '$', " + json.dumps(code) + ")("
        + json.dumps(json_in) + ", $);\n"
        "process.stdout.write(JSON.stringify(out[0].json));\n"
    )
    done = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True, timeout=30
    )
    out: dict[str, Any] = json.loads(done.stdout)
    return out


@pytest.mark.parametrize("mode", ["off", "nightly"])
def test_workflow_mode_without_qa_run_keeps_nodes_and_connections_byte_for_byte(
    qwp: ModuleType, mode: str
) -> None:
    workflow = _workflow()
    before = json.dumps(workflow)
    assert qwp.patch_workflow(workflow, mode) is False
    assert json.dumps(workflow) == before


@pytest.mark.parametrize("mode", ["final", "ui_tasks"])
def test_workflow_mode_adds_the_qa_nodes_after_final_acceptance(qwp: ModuleType, mode: str) -> None:
    workflow = _workflow()
    assert qwp.patch_workflow(workflow, mode) is True
    names = [node["name"] for node in workflow["nodes"]]
    assert names[-6:] == [
        "QA Changed Files", "QA Gate", "QA Run?", "QA Run", "QA Review", "QA Review?",
    ]
    connections = workflow["connections"]
    assert connections["All Tasks Completed?"] == {
        "main": [[_link("QA Changed Files")], [_link("Retry")]],
    }
    assert connections["QA Changed Files"]["main"] == [[_link("QA Gate")]]
    assert connections["QA Gate"]["main"] == [[_link("QA Run?")]]
    assert connections["QA Run?"]["main"] == [[_link("QA Run")], [_link("Approved Result")]]
    assert connections["QA Review?"]["main"] == [
        [_link("Parse Review")],
        [_link("Approved Result")],
    ]


@pytest.mark.parametrize("mode", ["final", "ui_tasks"])
def test_workflow_second_apply_changes_nothing(qwp: ModuleType, mode: str) -> None:
    workflow = _workflow()
    qwp.patch_workflow(workflow, mode)
    once = json.dumps(workflow)
    assert qwp.patch_workflow(workflow, mode) is False
    assert json.dumps(workflow) == once


def test_workflow_missing_final_node_aborts(qwp: ModuleType) -> None:
    workflow = _workflow()
    workflow["nodes"] = [n for n in workflow["nodes"] if n["name"] != "All Tasks Completed?"]
    with pytest.raises(qwp.AnchorError, match="must occur once"):
        qwp.patch_workflow(workflow, "final")


def test_workflow_final_node_without_false_branch_aborts(qwp: ModuleType) -> None:
    workflow = _workflow()
    workflow["connections"]["All Tasks Completed?"] = {"main": [[_link("Approved Result")]]}
    with pytest.raises(qwp.AnchorError, match="two main outputs"):
        qwp.patch_workflow(workflow, "final")


def test_workflow_duplicate_review_node_aborts(qwp: ModuleType) -> None:
    workflow = _workflow()
    workflow["nodes"].append(copy.deepcopy(workflow["nodes"][1]))
    with pytest.raises(qwp.AnchorError, match="found 2"):
        qwp.patch_workflow(workflow, "final")


@pytest.mark.parametrize(
    ("changed", "expected"),
    [
        (["claude-runner/src/claude_runner/board.html"], True),
        (["claude-runner/src/claude_runner/board_edit_win.js"], True),
        (["claude-runner/src/claude_runner/board.css"], True),
        (["claude-runner\\src\\claude_runner\\board_mission_save.js"], True),
        (["claude-runner/src/claude_runner/board_api.py"], True),
        (["claude-runner/src/claude_runner/dashboard.py"], True),
        (["claude-runner/src/claude_runner/routes/roles.py"], True),
        (["claude-runner/src/claude_runner/board_pg.py"], False),
        (["claude-runner/src/claude_runner/qa.py"], False),
        (["claude-runner/tests/test_board_api.py"], False),
        (["claude-runner/docs/qa-acceptance.md"], False),
        (["claude-runner/src/claude_runner/roles_config.py", "reports/x.txt"], False),
        ([], False),
    ],
)
def test_qa_gate_runs_only_for_ui_file_lists(
    qwp: ModuleType, changed: list[str], expected: bool
) -> None:
    out = _run_js(qwp.QA_GATE_JS, {"stdout": "\n".join(changed) + "\n"})
    assert out["qa_run"] is expected
    assert out["changed_files"] == changed


def test_qa_changed_files_command_lists_tracked_and_untracked_files(qwp: ModuleType) -> None:
    workflow = _workflow()
    qwp.patch_workflow(workflow, "final")
    node = next(n for n in workflow["nodes"] if n["name"] == "QA Changed Files")
    command = node["parameters"]["command"]
    assert node["type"] == "n8n-nodes-base.executeCommand"
    assert "git diff --name-only HEAD" in command
    assert "git ls-files --others --exclude-standard" in command
    assert "onError" not in node


def test_qa_review_pass_produces_no_review(qwp: ModuleType) -> None:
    out = _run_js(qwp.QA_REVIEW_JS, {"stdout": json.dumps([{"verdict": "pass"}])})
    assert out["qa_failed"] is False
    assert out["qa_review"] is None


def test_qa_review_fail_becomes_needs_changes_on_last_ui_task(qwp: ModuleType) -> None:
    verdicts = [{"verdict": "fail", "failed_step": "click save", "failed_reason": "button missing"}]
    tasks: list[dict[str, Any]] = [
        {"id": "T1", "ui_check": {"needed": False}},
        {"id": "T2", "ui_check": {"needed": True}},
        {"id": "T3"},
    ]
    out = _run_js(qwp.QA_REVIEW_JS, {"stdout": json.dumps(verdicts)}, tasks)
    assert out["qa_failed"] is True
    assert out["task_id"] == "T2"
    assert out["qa_review"]["status"] == "needs_changes"
    assert "click save" in out["qa_review"]["next_task"]
    assert "button missing" in out["qa_review"]["next_task"]


def test_qa_review_blocked_becomes_needs_changes(qwp: ModuleType) -> None:
    verdicts = [{"verdict": "blocked", "failed_reason": "no stack"}]
    out = _run_js(qwp.QA_REVIEW_JS, {"stdout": json.dumps(verdicts)}, [{"id": "T1"}])
    assert out["qa_failed"] is True
    assert out["task_id"] == "T1"
    assert out["qa_review"]["status"] == "needs_changes"
    assert out["qa_review"]["next_task"].startswith("QA could not run")


@pytest.mark.parametrize(
    "json_in",
    [
        {"stdout": ""},
        {"stdout": "not json", "stderr": "traceback"},
        {"stdout": "[]"},
        {"error": "boom"},
    ],
)
def test_qa_review_without_a_verdict_becomes_blocked_needs_changes(
    qwp: ModuleType, json_in: dict[str, Any]
) -> None:
    out = _run_js(qwp.QA_REVIEW_JS, json_in, [{"id": "T1"}])
    assert out["qa_failed"] is True
    assert out["task_id"] == "T1"
    assert out["qa_review"]["status"] == "needs_changes"
    assert out["qa_review"]["reason"] == "QA blocked"
    assert out["qa_review"]["next_task"].startswith("QA could not run: QA produced no verdict")
