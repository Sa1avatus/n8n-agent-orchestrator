from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

OPS_DIR = Path(__file__).resolve().parents[2] / "data-tables" / "ops"
PATCH_PATH = OPS_DIR / "qa_workflow_patch.py"
LIVE_EXPORT = OPS_DIR / "_scratch" / "live_export.json"
needs_live_export = pytest.mark.skipif(
    not LIVE_EXPORT.is_file(), reason=f"live export not found: {LIVE_EXPORT}"
)
EXECUTE_COMMAND = "n8n-nodes-base.executeCommand"
LAYERS_JSON = '["regression", "api", "ui"]'
GOLDEN_REQUEST_BODY = (
    "={{ JSON.stringify({ mission_id: $('Mission').first().json.mission_id, "
    "working_directory: $('Mission').first().json.working_directory, "
    'mode: "final", '
    "nonce: $execution.id + '-' + $runIndex }) }}"
)
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


@pytest.fixture
def live_copy(tmp_path: Path) -> Iterator[Path]:
    source_sha256 = _sha256(LIVE_EXPORT)
    copy = tmp_path / "live_export.json"
    shutil.copyfile(LIVE_EXPORT, copy)
    yield copy
    assert _sha256(LIVE_EXPORT) == source_sha256


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _insert_point(text: str, anchor: str) -> tuple[str, str]:
    head, tail = text.split(anchor, 1)
    return head, anchor + tail


def _live_workflow(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    workflow: dict[str, Any] = raw[0] if isinstance(raw, list) else raw
    return workflow


def _run_main(
    qwp: ModuleType, monkeypatch: pytest.MonkeyPatch, path: Path, mode: str, *extra: str
) -> None:
    monkeypatch.setattr(sys, "argv", ["qa_workflow_patch.py", mode, str(path), *extra])
    qwp.main()


def _request_body(workflow: dict[str, Any], qwp: ModuleType) -> str:
    node = next(n for n in workflow["nodes"] if n["name"] == qwp.QA_REQUEST)
    body: str = node["parameters"]["jsonBody"]
    return body


def _branch(name: str) -> list[dict[str, Any]]:
    return [{"node": name, "type": "main", "index": 0}]


def _node(name: str, node_type: str) -> dict[str, Any]:
    return {
        "name": name,
        "type": node_type,
        "typeVersion": 1,
        "position": [0, 0],
        "parameters": {},
    }


def _add_old_qa_nodes(workflow: dict[str, Any]) -> None:
    workflow["nodes"].extend(
        [
            _node("QA Changed Files", EXECUTE_COMMAND),
            _node("QA Gate", "n8n-nodes-base.code"),
            _node("QA Run?", "n8n-nodes-base.if"),
            _node("QA Run", EXECUTE_COMMAND),
            _node("QA Review?", "n8n-nodes-base.if"),
        ]
    )
    connections = workflow["connections"]
    connections["All Tasks Completed?"]["main"][0] = _branch("QA Changed Files")
    connections["QA Changed Files"] = {"main": [_branch("QA Gate")]}
    connections["QA Gate"] = {"main": [_branch("QA Run?")]}
    connections["QA Run?"] = {"main": [_branch("QA Run"), _branch("Approved Result")]}
    connections["QA Run"] = {"main": [_branch("QA Review?")]}
    connections["QA Review?"] = {"main": [_branch("Approved Result"), _branch("Approved Result")]}


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


@needs_live_export
@pytest.mark.parametrize("mode", ["off", "nightly"])
def test_mode_off_and_nightly_leave_the_export_byte_for_byte(
    qwp: ModuleType,
    mode: str,
    live_copy: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    golden_sha256 = _sha256(LIVE_EXPORT)
    _run_main(qwp, monkeypatch, live_copy, mode)
    assert _sha256(live_copy) == golden_sha256
    assert capsys.readouterr().out.endswith("no change\n")


@needs_live_export
@pytest.mark.parametrize("mode", ["final", "ui_tasks"])
def test_final_mode_inserts_each_qa_node_exactly_once(
    qwp: ModuleType,
    mode: str,
    live_copy: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run_main(qwp, monkeypatch, live_copy, mode)
    assert capsys.readouterr().out.endswith("QA nodes patched\n")
    workflow = _live_workflow(live_copy)
    counts = Counter(node["name"] for node in workflow["nodes"])
    for name in (qwp.QA_REQUEST, qwp.QA_WAIT, qwp.QA_REVIEW, qwp.QA_NOTICE):
        assert counts[name] == 1, name
    assert all(count == 1 for name, count in counts.items() if name.startswith("QA "))
    assert workflow["connections"][qwp.FINAL_NODE]["main"][0] == _branch(qwp.QA_REQUEST)
    retrieval = _branch("Worker Retrieval Enabled?")
    assert workflow["connections"][qwp.FINAL_NODE]["main"][1] == retrieval


@needs_live_export
def test_second_run_on_the_patched_export_changes_nothing(
    qwp: ModuleType,
    live_copy: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run_main(qwp, monkeypatch, live_copy, "final")
    once_sha256 = _sha256(live_copy)
    _run_main(qwp, monkeypatch, live_copy, "final")
    assert _sha256(live_copy) == once_sha256
    assert capsys.readouterr().out.endswith("no change\n")


@needs_live_export
def test_patched_export_has_no_execute_command_node(qwp: ModuleType, live_copy: Path) -> None:
    workflow = _live_workflow(live_copy)
    assert qwp.patch_workflow(workflow, "final") is True
    assert [n["name"] for n in workflow["nodes"] if n["type"] == EXECUTE_COMMAND] == []


@needs_live_export
def test_smoke_off_body_is_byte_for_byte_golden(qwp: ModuleType, live_copy: Path) -> None:
    workflow = _live_workflow(live_copy)
    assert qwp.patch_workflow(workflow, "final") is True
    once_body = _request_body(workflow, qwp)
    fresh = _live_workflow(live_copy)
    assert qwp.patch_workflow(fresh, "final", None, False) is True
    twice_body = _request_body(fresh, qwp)
    assert once_body == GOLDEN_REQUEST_BODY
    assert once_body == twice_body


@needs_live_export
def test_smoke_true_adds_smoke_to_the_request_body(qwp: ModuleType, live_copy: Path) -> None:
    workflow = _live_workflow(live_copy)
    assert qwp.patch_workflow(workflow, "final", None, True) is True
    body = _request_body(workflow, qwp)
    assert 'mode: "final", smoke: true, nonce: ' in body
    assert "layers" not in body


@needs_live_export
def test_second_apply_with_smoke_true_changes_nothing(
    qwp: ModuleType, live_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_main(qwp, monkeypatch, live_copy, "final", LAYERS_JSON, "true")
    once_sha256 = _sha256(live_copy)
    _run_main(qwp, monkeypatch, live_copy, "final", LAYERS_JSON, "true")
    assert _sha256(live_copy) == once_sha256


@needs_live_export
def test_missing_or_duplicated_anchor_node_raises(qwp: ModuleType, live_copy: Path) -> None:
    workflow = _live_workflow(live_copy)
    for name in workflow["nodes"]:
        if name["name"] == "Approved Result":
            del workflow["nodes"][workflow["nodes"].index(name)]
            break
    with pytest.raises(qwp.AnchorError, match="must occur once in the workflow, found 0"):
        qwp.patch_workflow(workflow, "final", None, True)

    fresh = _live_workflow(live_copy)
    fresh["nodes"].append(_node("Approved Result", "n8n-nodes-base.noOp"))
    with pytest.raises(qwp.AnchorError, match="must occur once in the workflow, found 2"):
        qwp.patch_workflow(fresh, "final", None, True)


@needs_live_export
def test_pre_patched_old_node_set_is_removed(qwp: ModuleType, live_copy: Path) -> None:
    workflow = _live_workflow(live_copy)
    _add_old_qa_nodes(workflow)
    assert qwp.patch_workflow(workflow, "final") is True
    names = Counter(node["name"] for node in workflow["nodes"])
    for old in qwp.OLD_NODES:
        assert names[old] == 0, old
    assert [n["name"] for n in workflow["nodes"] if n["type"] == EXECUTE_COMMAND] == []
    assert names[qwp.QA_REVIEW] == 1
    assert "QA Changed Files" not in workflow["connections"]
    assert workflow["connections"][qwp.FINAL_NODE]["main"][0] == _branch(qwp.QA_REQUEST)


@needs_live_export
@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda w: w.__setitem__(
                "nodes", [n for n in w["nodes"] if n["name"] != "All Tasks Completed?"]
            ),
            "'All Tasks Completed?' must occur once in the workflow, found 0",
        ),
        (
            lambda w: w.__setitem__(
                "nodes", [n for n in w["nodes"] if n["name"] != "Approved Result"]
            ),
            "'Approved Result' must occur once in the workflow, found 0",
        ),
        (
            lambda w: w.__setitem__(
                "nodes", [n for n in w["nodes"] if n["name"] != "Retry Current Task"]
            ),
            "'Retry Current Task' must occur once in the workflow, found 0",
        ),
        (
            lambda w: w["connections"]["All Tasks Completed?"]["main"][0].__setitem__(
                0, {"node": "Retry Current Task", "type": "main", "index": 0}
            ),
            "must lead to 'Approved Result'",
        ),
    ],
)
def test_missing_or_moved_anchor_in_the_export_aborts(
    qwp: ModuleType, live_copy: Path, mutate: Any, message: str
) -> None:
    workflow = _live_workflow(live_copy)
    mutate(workflow)
    with pytest.raises(qwp.AnchorError, match=re.escape(message)):
        qwp.patch_workflow(workflow, "final")


@needs_live_export
def test_constants_without_runner_settings_aborts(qwp: ModuleType, live_copy: Path) -> None:
    workflow = _live_workflow(live_copy)
    constants = next(n for n in workflow["nodes"] if n["name"] == "Constants")
    constants["parameters"] = {}
    constants.pop("assignments", None)
    with pytest.raises(qwp.AnchorError, match="must define runner_url and runner_api_key"):
        qwp.patch_workflow(workflow, "final")


@needs_live_export
def test_inserted_code_nodes_pass_node_check(
    qwp: ModuleType, live_copy: Path, tmp_path: Path
) -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    workflow = _live_workflow(live_copy)
    assert qwp.patch_workflow(workflow, "final") is True
    code_nodes = [
        n for n in workflow["nodes"] if n["type"] == qwp.CODE_NODE and n["name"].startswith("QA ")
    ]
    assert {n["name"] for n in code_nodes} == {qwp.QA_POLL_COUNT, qwp.QA_REVIEW}
    for index, node in enumerate(code_nodes):
        script = tmp_path / f"inserted_{index}.js"
        script.write_text(node["parameters"]["jsCode"], encoding="utf-8")
        subprocess.run(
            ["node", "--check", str(script)], check=True, capture_output=True, timeout=30
        )


@needs_live_export
@pytest.mark.parametrize("extra", [(), ("[]",)])
def test_missing_or_empty_layers_keep_the_request_body_as_before(
    qwp: ModuleType, extra: tuple[str, ...], live_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_main(qwp, monkeypatch, live_copy, "final", *extra)
    assert _request_body(_live_workflow(live_copy), qwp) == GOLDEN_REQUEST_BODY


@needs_live_export
def test_mode_off_ignores_layers(
    qwp: ModuleType, live_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    golden_sha256 = _sha256(LIVE_EXPORT)
    _run_main(qwp, monkeypatch, live_copy, "off", LAYERS_JSON)
    assert _sha256(live_copy) == golden_sha256


@needs_live_export
def test_layers_from_roles_json_appear_in_the_request_body(
    qwp: ModuleType, live_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run_main(qwp, monkeypatch, live_copy, "final", LAYERS_JSON)
    body = _request_body(_live_workflow(live_copy), qwp)
    assert 'mode: "final", layers: ["regression", "api", "ui"], nonce: ' in body


@needs_live_export
def test_second_run_with_layers_changes_nothing(
    qwp: ModuleType,
    live_copy: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _run_main(qwp, monkeypatch, live_copy, "final", LAYERS_JSON)
    once_sha256 = _sha256(live_copy)
    _run_main(qwp, monkeypatch, live_copy, "final", LAYERS_JSON)
    assert _sha256(live_copy) == once_sha256
    assert capsys.readouterr().out.endswith("no change\n")


@pytest.mark.parametrize(
    ("layers", "message"),
    [
        (["sql"], "layer 'sql' is not one of regression, api, ui"),
        ("api", "layers must be a JSON list of layer names"),
        ([1], "layers must be a JSON list of layer names"),
    ],
)
def test_bad_layers_are_rejected(qwp: ModuleType, layers: Any, message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        qwp.patch_workflow({"nodes": [], "connections": {}}, "final", layers)
