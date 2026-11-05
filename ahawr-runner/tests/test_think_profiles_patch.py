"""Tests for data-tables/ops/think_profiles_patch.py and its Architect-prompt companion:
the Worker launch node gets think_profile exactly once, a second run changes nothing, and a
missing or duplicated anchor raises. data-tables/ops is not in git, so every test skips with a
reason when the file is absent. The workflow fixture is synthetic: made-up node bodies only.

Run: python -m pytest tests/test_think_profiles_patch.py -q --no-cov
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

OPS_DIR = Path(__file__).resolve().parents[2] / "data-tables" / "ops"
PATCH_PATH = OPS_DIR / "think_profiles_patch.py"
PROMPT_PATH = OPS_DIR / "architect_prompt_think_profiles.py"
NL = "\n"


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def patcher() -> ModuleType:
    if not PATCH_PATH.is_file():
        pytest.skip(f"ops patch not present (ops is not in git): {PATCH_PATH}")
    return load_module("think_profiles_patch", PATCH_PATH)


def worker_node_code(p: ModuleType) -> str:
    # a stand-in for the Worker launch node: only the two anchors, no real workflow data
    return NL.join([
        "const __run = $input.first().json;",
        "const s = __run.state;",
        p.TASK_ANCHOR,
        "const retryTask = s.tasks[0];",
        "const hygieneOn = Boolean(s.hygiene);",
        "const request = {",
        p.REQUEST_ANCHOR,
        "};",
        "return [{ json: request }];",
    ])


def make_workflow(p: ModuleType) -> dict[str, Any]:
    return {"nodes": [{"name": p.NODE_NAME, "parameters": {"jsCode": worker_node_code(p)}}]}


def jscode(workflow: dict[str, Any]) -> str:
    return str(workflow["nodes"][0]["parameters"]["jsCode"])


def test_think_profile_field_added_once(patcher: ModuleType) -> None:
    workflow = make_workflow(patcher)
    assert patcher.patch_workflow(workflow) == "added think_profile spread"
    code = jscode(workflow)
    assert code.count(patcher.MARKER) == 1
    assert code.count("const __thinking =") == 1
    assert code.count("...(__thinking ? { think_profile: __thinking } : {})") == 1
    assert code.count(patcher.REQUEST_ANCHOR) == 1


def test_second_run_changes_nothing(patcher: ModuleType) -> None:
    workflow = make_workflow(patcher)
    patcher.patch_workflow(workflow)
    before = jscode(workflow)
    assert patcher.patch_workflow(workflow) == "already patched"
    assert jscode(workflow) == before


def test_missing_anchor_raises_and_keeps_the_code(patcher: ModuleType) -> None:
    code = worker_node_code(patcher).replace(patcher.TASK_ANCHOR, "")
    workflow = {"nodes": [{"name": patcher.NODE_NAME, "parameters": {"jsCode": code}}]}
    with pytest.raises(SystemExit, match=re.escape("anchor for task lookup occurs 0 times")):
        patcher.patch_workflow(workflow)
    assert jscode(workflow) == code


def test_duplicate_anchor_raises(patcher: ModuleType) -> None:
    for anchor, what in (
        (patcher.TASK_ANCHOR, "task lookup"),
        (patcher.REQUEST_ANCHOR, "request spread"),
    ):
        code = worker_node_code(patcher) + NL + anchor
        workflow = {"nodes": [{"name": patcher.NODE_NAME, "parameters": {"jsCode": code}}]}
        with pytest.raises(SystemExit, match=re.escape(f"anchor for {what} occurs 2 times")):
            patcher.patch_workflow(workflow)
        assert jscode(workflow) == code


def test_worker_launch_node_missing_raises(patcher: ModuleType) -> None:
    with pytest.raises(SystemExit, match=re.escape("found 0 times, expected exactly 1")):
        patcher.patch_workflow({"nodes": []})


def test_worker_launch_node_duplicated_raises(patcher: ModuleType) -> None:
    node = {"name": patcher.NODE_NAME, "parameters": {"jsCode": worker_node_code(patcher)}}
    with pytest.raises(SystemExit, match=re.escape("found 2 times, expected exactly 1")):
        patcher.patch_workflow({"nodes": [node, node]})


def run_architect_script(
    system_prompt: str, tmp_path: Path
) -> tuple[str, list[str], dict[str, Any]]:
    """Run the ops append script with pgdb stubbed out; return printed lines, the prompts it
    sent to the table, and the namespace so the test can read MARK, ANCHOR and RULE."""

    def fake_table(table: str) -> str:
        return table

    updates: list[str] = []
    output: list[str] = []

    def fake_q(sql: str, params: list[str]) -> list[dict[str, str]]:
        return [{"system_prompt": system_prompt}]

    def fake_x(sql: str, params: list[str]) -> int:
        updates.append(params[0])
        return 1

    fake_pgdb = ModuleType("pgdb")
    for name, value in (("T", fake_table), ("q", fake_q), ("x", fake_x)):
        setattr(fake_pgdb, name, value)
    namespace: dict[str, Any] = {
        # __file__ under tmp_path so the script's OPS and BACKUP resolve inside the fixture dir
        "__file__": str(tmp_path / PROMPT_PATH.name),
        "print": lambda *args: output.append(" ".join(str(arg) for arg in args)),
    }
    saved_module = sys.modules.get("pgdb")
    saved_path = list(sys.path)
    sys.modules["pgdb"] = fake_pgdb
    try:
        exec(compile(PROMPT_PATH.read_text(encoding="utf-8"), str(PROMPT_PATH), "exec"), namespace)
    finally:
        if saved_module is None:
            del sys.modules["pgdb"]
        else:
            sys.modules["pgdb"] = saved_module
        sys.path[:] = saved_path
    return "\n".join(output), updates, namespace


def test_architect_rule_appended_once_and_never_twice(tmp_path: Path) -> None:
    if not PROMPT_PATH.is_file():
        pytest.skip(f"ops prompt script not present (ops is not in git): {PROMPT_PATH}")
    prompt = (
        'You are the Architect. The top-level "tasks" array MUST be non-empty. Answer as JSON.\n'
    )
    output, updates, namespace = run_architect_script(prompt, tmp_path)
    mark = str(namespace["MARK"])
    anchor = str(namespace["ANCHOR"])
    assert len(updates) == 1, "the script did not write the prompt"
    patched = updates[0]
    assert patched.count(mark) == 1
    assert patched.count(anchor) == 1
    assert patched.index(mark) < patched.index(anchor)
    backup = tmp_path / "architect_prompt_before_think_profiles_rule.txt"
    assert backup.read_text(encoding="utf-8") == prompt
    # the same script on the patched prompt: the MARK guard fires, nothing is sent again
    second_output, second_updates, _ = run_architect_script(patched, tmp_path)
    assert not second_updates
    assert "already" in second_output
    assert backup.read_text(encoding="utf-8") == prompt  # the backup is written exactly once
