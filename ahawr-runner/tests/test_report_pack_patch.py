from __future__ import annotations

import copy
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PATCH_PATH = Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "report_pack_patch.py"
NL = "\n"
TEMPLATE = "TEMPLATE for <mission_id>"
RUBRIC = "RUBRIC"
NODE_AVAILABLE = shutil.which("node") is not None

HARNESS = """
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const $ = (name) => {
  const node = input.nodes[name];
  if (node === undefined) throw new Error('no stub for node ' + name);
  return {
    isExecuted: node.executed,
    first: () => ({ json: node.json }),
    last: () => ({ json: node.json }),
  };
};
const helpers = { httpRequest: async ({ url }) => {
  const run = (input.runs || {})[url.split('/').pop()];
  if (run === undefined) throw new Error('no stub for run ' + url);
  return run;
} };
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
new AsyncFunction('$', input.code).call({ helpers }, $)
  .then((out) => process.stdout.write(JSON.stringify(out)));
"""


def load_patch() -> ModuleType:
    spec = importlib.util.spec_from_file_location("report_pack_patch", PATCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if not PATCH_PATH.exists():
    pytest.skip(
        f"data-tables/ops is not in git and {PATCH_PATH} is absent", allow_module_level=True
    )

patcher = load_patch()
CONSTANTS = "Constants"
ARCHITECT = "Build Architect Run Input"
WORKER = "Build Worker Run Input"
REVIEWER = "Build Reviewer Run Input"
PARSE = "Parse Review"
NOTICE = "Split Task Notice"
PATCHED_NODES = [CONSTANTS, ARCHITECT, WORKER, REVIEWER, PARSE, NOTICE]
MISSION = {
    "mission_id": "m1",
    "mission_name": "Mission one",
    "working_directory": "/w",
    "acceptance_criteria": ["works"],
}
CONFIG_OFF = {"report_pack": 0, "runner_url": "http://runner", "runner_api_key": ""}
CONFIG_ON = {**CONFIG_OFF, "report_pack": 1}
BUILD_TASKS = [{"id": "task-001", "title": "Build", "kind": "build", "objective": "o"}]

needs_node = pytest.mark.skipif(not NODE_AVAILABLE, reason="node is not on PATH")


def fixture() -> dict[str, Any]:
    constants = NL.join([
        "const row = {};",
        "const mission = {};",
        "return [{ json: {",
        "  mission_roles_json: String(mission.roles_json ?? '').trim()",
        "} }];",
    ])
    architect = NL.join([
        "const parts = [",
        "    " + patcher.ARCH_OLD,
        "];",
        "return [{ json: { architect: parts.join('') } }];",
    ])
    reviewer = NL.join([
        "const parts = [",
        patcher.REVIEWER_OLD,
        "];",
        "return [{ json: { reviewer: parts.join('') } }];",
    ])
    worker = NL.join([
        "const s = $('Trigger').first().json;",
        "const fullInput = 'full';",
        "const resumeText = 'resume';",
        "const unfinished = false;",
        "const __cfg = { fresh: true, model: 'haiku', provider: 'anthropic' };",
        "const __roleConfig = () => __cfg;",
        patcher.WORKER_FRESH_OLD,
        "return [{ json: {",
        patcher.WORKER_MODEL_OLD,
        patcher.WORKER_PROVIDER_OLD,
        patcher.WORKER_INPUT_OLD,
        "} }];",
    ])
    parse = NL.join([
        "let review = { score: 1, status: 'pass' };",
        patcher.PARSE_OLD,
        "} }];",
    ])
    notice = NL.join([
        "const t = { kind: 'build', title: 'x', objective: 'o' };",
        "const j = { tasks: [], current_task_id: 't' };",
        "const attempt = 1;",
        "const sections = [];",
        patcher.NOTICE_OLD,
        "return [{ json: { notice: sections.join('') } }];",
    ])
    codes = {
        CONSTANTS: constants,
        ARCHITECT: architect,
        WORKER: worker,
        REVIEWER: reviewer,
        PARSE: parse,
        NOTICE: notice,
    }
    return {"nodes": [{"name": name, "parameters": {"jsCode": code}}
                      for name, code in codes.items()]}


def code_of(w: dict[str, Any], name: str) -> str:
    return str(patcher.node(w, name)["parameters"]["jsCode"])


def run_raw(code: str, nodes: dict[str, Any], runs: dict[str, Any] | None = None) -> str:
    payload = json.dumps({"code": code, "nodes": nodes, "runs": runs or {}})
    done = subprocess.run(
        ["node", "-e", HARNESS],
        input=payload,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return done.stdout


def stubs(config: dict[str, Any], tasks: list[dict[str, Any]], index: int) -> dict[str, Any]:
    trigger = {"tasks": tasks, "task_index": index, "task_attempt": 1}
    return {
        CONSTANTS: {"executed": True, "json": config},
        "Trigger": {"executed": True, "json": trigger},
        "Mission": {"executed": True, "json": MISSION},
        "Prompt Templates": {
            "executed": True,
            "json": {"architect_prompt": "ARCH PROMPT", "reviewer_prompt": "REVIEW PROMPT"},
        },
        ARCHITECT: {"executed": False, "json": {}},
        WORKER: {"executed": False, "json": {}},
        REVIEWER: {"executed": False, "json": {}},
    }


def evaluate(w: dict[str, Any], name: str, config: dict[str, Any],
             tasks: list[dict[str, Any]] = BUILD_TASKS, index: int = 0,
             runs: dict[str, Any] | None = None) -> str:
    return run_raw(code_of(w, name), stubs(config, tasks, index), runs)


def test_second_run_changes_nothing() -> None:
    w = fixture()
    assert patcher.patch(w, TEMPLATE, RUBRIC) == PATCHED_NODES
    before = copy.deepcopy(w)
    assert patcher.patch(w, TEMPLATE, RUBRIC) == []
    assert w == before


@pytest.mark.parametrize(
    ("name", "anchor"),
    [
        (CONSTANTS, patcher.CONSTANTS_OLD),
        (ARCHITECT, patcher.ARCH_OLD),
        (WORKER, patcher.WORKER_FRESH_OLD),
        (WORKER, patcher.WORKER_MODEL_OLD),
        (WORKER, patcher.WORKER_PROVIDER_OLD),
        (WORKER, patcher.WORKER_INPUT_OLD),
        (REVIEWER, patcher.REVIEWER_OLD),
        (PARSE, patcher.PARSE_OLD),
        (NOTICE, patcher.NOTICE_OLD),
    ],
)
@pytest.mark.parametrize("copies", [0, 2])
def test_anchor_must_occur_exactly_once(name: str, anchor: str, copies: int) -> None:
    w = fixture()
    node_params = patcher.node(w, name)["parameters"]
    doubled = anchor + NL + anchor if copies else ""
    node_params["jsCode"] = node_params["jsCode"].replace(anchor, doubled)
    with pytest.raises(AssertionError, match=re.escape(f"found exactly once ({copies})")):
        patcher.patch(w, TEMPLATE, RUBRIC)


@needs_node
def test_flag_off_prompts_are_byte_identical_to_unpatched() -> None:
    unpatched = fixture()
    patched = fixture()
    patcher.patch(patched, TEMPLATE, RUBRIC)
    for name in (ARCHITECT, WORKER, REVIEWER):
        assert patcher.MARK in code_of(patched, name)
    for name in (ARCHITECT, WORKER, REVIEWER):
        for tasks in (BUILD_TASKS, [{"id": "t", "title": "Report", "kind": "report"}]):
            runs = {"report-facts": {"markdown": "FACTS"}}
            assert (evaluate(patched, name, CONFIG_OFF, tasks, 0, runs)
                    == evaluate(unpatched, name, CONFIG_OFF, tasks, 0, runs)), name


@needs_node
def test_flag_on_non_report_plan_leaves_worker_input_untouched() -> None:
    unpatched = fixture()
    patched = fixture()
    patcher.patch(patched, TEMPLATE, RUBRIC)
    tasks = [
        {"id": "t1", "title": "Write", "kind": "report"},
        {"id": "t2", "title": "Build", "kind": "build"},
    ]
    assert (evaluate(patched, WORKER, CONFIG_ON, tasks, 1)
            == evaluate(unpatched, WORKER, CONFIG_ON, tasks, 1))


@needs_node
@pytest.mark.parametrize(
    ("tasks", "index", "is_report"),
    [
        ([{"id": "t1", "title": "Write", "kind": "report"}, {"id": "t2", "title": "Build",
          "kind": "build"}], 0, True),
        ([{"id": "t1", "title": "Build", "kind": "build"}, {"id": "t2", "title": "Write",
          "kind": "report"}], 1, True),
        ([{"id": "t1", "title": "Build", "kind": "build"}, {"id": "t2", "title": "Отчёт",
          "kind": "build"}], 1, True),
        ([{"id": "t1", "title": "Report", "kind": "build"}, {"id": "t2", "title": "Build",
          "kind": "build"}], 0, False),
        (BUILD_TASKS, 0, False),
    ],
)
def test_report_task_detection(tasks: list[dict[str, Any]], index: int, is_report: bool) -> None:
    w = fixture()
    patcher.patch(w, TEMPLATE, RUBRIC)
    runs = {"report-facts": {"markdown": "FACTS"}}
    out = json.loads(evaluate(w, WORKER, CONFIG_ON, tasks, index, runs))
    assert ("REPORT FILE" in out[0]["json"]["input"]) is is_report
