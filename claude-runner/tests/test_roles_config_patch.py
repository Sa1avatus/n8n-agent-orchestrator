from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PATCH_PATH = Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "roles_config_patch.py"
NODE_AVAILABLE = shutil.which("node") is not None
NL = "\n"

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
process.stdout.write(JSON.stringify(new Function('$', input.code)($)));
"""

CONSTANTS = "Constants"
ARCHITECT = "Build Architect Run Input"
WORKER = "Build Worker Run Input"
REVIEWER = "Build Reviewer Run Input"
SPLIT = "Split Task Notice"
REVIEW = "Restore Review Notification Context"
VERDICT = "Prepare Verdict Retry"
PLAN_MESSAGE = "Send a text message5"
FAILED_MESSAGE = "Send a text message6"
APPROVED_MESSAGE = "Send a text message7"
PATCHED_NODES = [
    CONSTANTS, ARCHITECT, WORKER, REVIEWER, SPLIT, REVIEW, VERDICT,
    PLAN_MESSAGE, FAILED_MESSAGE, APPROVED_MESSAGE,
]

CONSTANTS_OLD = "Number(row.worker_think_off_after_steps)" + NL + "} }];"
ARCH_MODEL = "  model: ($('Constants').first().json.planner_model),"
ARCH_PROVIDER = "  provider: ($('Constants').first().json.planner_provider),"
REVIEWER_MODEL = "  model: ($('Constants').first().json.reviewer_model),"
REVIEWER_PROVIDER = "  provider: ($('Constants').first().json.reviewer_provider),"
SESSION_LINE = "  session_id: (__newSession ? '' : String(s.worker_session_id ?? '')),"
INPUT_LINE = "  input: __newSession ? fullInput : (unfinished ? resumeText : fullInput),"
SPLIT_OLD = (
    "try { const c = $('Constants').first().json; model = f(c.worker_model, c.worker_provider); }"
    " catch (_) {}"
)
VERDICT_OLD = "  model: cfg.reviewer_model, provider: cfg.reviewer_provider,"
REVIEW_OLD = (
    "const c = $('Constants').first().json; "
    "const f = (m, p) => String(m || '?') + (p ? ' @' + p : ''); "
    "return `🤖 Worker: ${f(c.worker_model, c.worker_provider)} · Reviewer "
    "${f(c.reviewer_model, c.reviewer_provider)}`;"
)

LEGACY_CONFIG = {
    "planner_provider": "anthropic",
    "planner_model": "opus",
    "worker_provider": "anthropic",
    "worker_model": "haiku",
    "reviewer_provider": "local",
    "reviewer_model": "qwen",
    "roles_json": "",
    "mission_roles_json": "",
}
CLOUD_CONFIG = {
    **LEGACY_CONFIG,
    "roles_json": json.dumps({"worker": {"1": "anthropic/haiku", "2": "local/qwen"}}),
}


def load_patch() -> ModuleType:
    spec = importlib.util.spec_from_file_location("roles_config_patch", PATCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


patcher = load_patch()


def message_code(role: str) -> str:
    return (
        f"const c=$('Constants').first().json;const f=(m,p)=>String(m||'?')+(p?' @'+p:'');"
        f"return f(c.{role}_model,c.{role}_provider);"
    )


def fixture_nodes() -> list[tuple[str, str, str]]:
    return [
        (CONSTANTS, "jsCode", NL.join([
            "const row = {};",
            "const mission = {};",
            "return [{ json: {",
            "  think: " + CONSTANTS_OLD,
        ])),
        (ARCHITECT, "jsCode", NL.join([
            "const planned = true;",
            "return [{ json: {",
            ARCH_MODEL,
            ARCH_PROVIDER,
            "} }];",
        ])),
        (WORKER, "jsCode", NL.join([
            "const s = $('Trigger').first().json;",
            "const fullInput = 'full';",
            "const resumeText = 'resume';",
            "const unfinished = false;",
            "// cloud_first_switch: attempt 2 uses the local model",
            "const __useLocal = Number(s.task_attempt ?? 1) > 1;",
            "const __newSession = !s.worker_session_id;",
            "const __unfinished = unfinished;",
            "const __resumeText = resumeText;",
            "return [{ json: {",
            SESSION_LINE,
            INPUT_LINE,
            "  model: (__useLocal ? 'qwen' : ($('Constants').first().json.worker_model)),",
            "  provider: (__useLocal ? 'local' : ($('Constants').first().json.worker_provider)),",
            "} }];",
        ])),
        (REVIEWER, "jsCode", NL.join([
            "const planned = true;",
            "return [{ json: {",
            REVIEWER_MODEL,
            REVIEWER_PROVIDER,
            "} }];",
        ])),
        (SPLIT, "jsCode", NL.join([
            "const attempt = 1;",
            "const f = (m, p) => String(m || '?') + (p ? ' @' + p : '');",
            "let model = '?';",
            SPLIT_OLD,
            "return [{ json: { model } }];",
        ])),
        (REVIEW, "jsCode", REVIEW_OLD),
        (VERDICT, "jsCode", NL.join([
            "const cfg = $('Constants').first().json;",
            "return [{ json: {",
            VERDICT_OLD,
            "} }];",
        ])),
        (PLAN_MESSAGE, "text", message_code("planner")),
        (FAILED_MESSAGE, "text", message_code("worker")),
        (APPROVED_MESSAGE, "text", message_code("worker")),
    ]


def make_workflow() -> dict[str, Any]:
    return {
        "nodes": [
            {"name": name, "parameters": {field: code}}
            for name, field, code in fixture_nodes()
        ]
    }


def edit_node(w: dict[str, Any], name: str, old: str, new: str) -> None:
    params = patcher.node_named(w, name)["parameters"]
    field = "jsCode" if "jsCode" in params else "text"
    assert params[field].count(old) == 1
    params[field] = params[field].replace(old, new, 1)


WORKER_MODEL_OLD = "  model: (__useLocal ? 'qwen' : ($('Constants').first().json.worker_model)),"
WORKER_PROVIDER_OLD = (
    "  provider: (__useLocal ? 'local' : ($('Constants').first().json.worker_provider)),"
)


def make_workflow_no_marker() -> dict[str, Any]:
    w = make_workflow()
    edit_node(w, WORKER, "return [{ json: {", "return { json: {")
    edit_node(w, WORKER, "} }];", "} };")
    edit_node(w, WORKER, "// cloud_first_switch: attempt 2 uses the local model", "")
    edit_node(w, WORKER, SESSION_LINE, patcher.WORKER_SESSION_OLD)
    edit_node(w, WORKER, INPUT_LINE, patcher.WORKER_INPUT_OLD)
    edit_node(w, WORKER, WORKER_MODEL_OLD, patcher.WORKER_MODEL_OLD)
    edit_node(w, WORKER, WORKER_PROVIDER_OLD, patcher.WORKER_PROVIDER_OLD)
    return w


def code_of(w: dict[str, Any], name: str) -> str:
    params = patcher.node_named(w, name)["parameters"]
    return str(params["jsCode"] if "jsCode" in params else params["text"])


def run_node(code: str, nodes: dict[str, dict[str, Any]]) -> Any:
    payload = json.dumps({"code": code, "nodes": nodes})
    done = subprocess.run(
        ["node", "-e", HARNESS],
        input=payload,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(done.stdout)


def stubs(config: dict[str, str], attempt: int) -> dict[str, dict[str, Any]]:
    return {
        CONSTANTS: {"executed": True, "json": config},
        "Trigger": {
            "executed": True,
            "json": {"task_attempt": attempt, "worker_session_id": "sess-1"},
        },
        "Reviewer State": {"executed": True, "json": {"task_attempt": attempt}},
        ARCHITECT: {"executed": False, "json": {}},
        WORKER: {"executed": False, "json": {}},
        REVIEWER: {"executed": False, "json": {}},
    }


def test_second_apply_changes_nothing() -> None:
    once = make_workflow()
    assert patcher.patch(once) == PATCHED_NODES
    twice = make_workflow()
    patcher.patch(twice)
    assert patcher.patch(twice) == []
    assert twice == once


@pytest.mark.parametrize(
    ("name", "anchor"),
    [
        (CONSTANTS, CONSTANTS_OLD),
        (ARCHITECT, ARCH_MODEL),
        (ARCHITECT, ARCH_PROVIDER),
        (REVIEWER, REVIEWER_MODEL),
        (REVIEWER, REVIEWER_PROVIDER),
        (WORKER, SESSION_LINE),
        (WORKER, INPUT_LINE),
        (SPLIT, SPLIT_OLD),
        (REVIEW, REVIEW_OLD),
        (VERDICT, VERDICT_OLD),
    ],
)
def test_missing_anchor_raises(name: str, anchor: str) -> None:
    w = make_workflow()
    edit_node(w, name, anchor, "")
    with pytest.raises(SystemExit, match=re.escape(f"{name}: anchor found 0 times")):
        patcher.patch(w)


@pytest.mark.parametrize(
    ("name", "anchor"),
    [
        (CONSTANTS, CONSTANTS_OLD),
        (ARCHITECT, ARCH_MODEL),
        (REVIEWER, REVIEWER_PROVIDER),
        (WORKER, SESSION_LINE),
        (SPLIT, SPLIT_OLD),
        (VERDICT, VERDICT_OLD),
    ],
)
def test_duplicate_anchor_raises(name: str, anchor: str) -> None:
    w = make_workflow()
    edit_node(w, name, anchor, anchor + NL + anchor)
    with pytest.raises(SystemExit, match=re.escape(f"{name}: anchor found 2 times")):
        patcher.patch(w)


def test_missing_node_raises() -> None:
    w = make_workflow()
    w["nodes"] = [n for n in w["nodes"] if n["name"] != ARCHITECT]
    with pytest.raises(SystemExit, match=re.escape(f"{ARCHITECT} node: 0 found")):
        patcher.patch(w)


@pytest.mark.skipif(not NODE_AVAILABLE, reason="node is not on PATH")
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (ARCHITECT, [{"json": {"model": "opus", "provider": "anthropic"}}]),
        (WORKER, [{"json": {
            "session_id": "sess-1", "input": "full", "model": "haiku", "provider": "anthropic",
        }}]),
        (REVIEWER, [{"json": {"model": "qwen", "provider": "local"}}]),
        (REVIEW, "🤖 Worker: haiku @anthropic · Reviewer qwen @local"),
    ],
)
def test_legacy_only_output_is_unchanged(name: str, expected: Any) -> None:
    before = make_workflow()
    after = make_workflow()
    patcher.patch(after)
    before_out = run_node(code_of(before, name), stubs(LEGACY_CONFIG, 1))
    after_out = run_node(code_of(after, name), stubs(LEGACY_CONFIG, 1))
    assert before_out == expected
    assert after_out == before_out


@pytest.mark.skipif(not NODE_AVAILABLE, reason="node is not on PATH")
def test_legacy_only_resolves_no_think_and_legacy_models() -> None:
    code = NL.join([
        patcher.core_block(),
        patcher.ROLE_CONFIG,
        "return { architect: __roleConfig('architect', 1), worker: __roleConfig('worker', 1),"
        " reviewer: __roleConfig('reviewer', 1) };",
    ])
    resolved = run_node(code, stubs(LEGACY_CONFIG, 1))
    assert {role: cfg["think"] for role, cfg in resolved.items()} == {
        "architect": None,
        "worker": None,
        "reviewer": None,
    }
    assert resolved["worker"]["provider"] == "anthropic"
    assert resolved["worker"]["model"] == "haiku"


@pytest.mark.skipif(not NODE_AVAILABLE, reason="node is not on PATH")
@pytest.mark.parametrize(
    ("attempt", "expected"),
    [
        (1, [{"json": {
            "session_id": "sess-1", "input": "full", "model": "haiku", "provider": "anthropic",
        }}]),
        (2, [{"json": {
            "session_id": "", "input": "full", "model": "qwen", "provider": "local",
        }}]),
    ],
)
def test_cloud_first_roles_switch_to_local_on_attempt_two(attempt: int, expected: Any) -> None:
    w = make_workflow()
    patcher.patch(w)
    out = run_node(code_of(w, WORKER), stubs(CLOUD_CONFIG, attempt))
    assert out == expected


def test_no_marker_second_apply_changes_nothing() -> None:
    once = make_workflow_no_marker()
    assert patcher.patch(once) == PATCHED_NODES
    twice = make_workflow_no_marker()
    patcher.patch(twice)
    assert patcher.patch(twice) == []
    assert twice == once


@pytest.mark.skipif(not NODE_AVAILABLE, reason="node is not on PATH")
@pytest.mark.parametrize(
    ("attempt", "expected"),
    [
        (1, {"json": {
            "session_id": "sess-1", "input": "full", "model": "haiku", "provider": "anthropic",
        }}),
        (2, {"json": {
            "session_id": "sess-1", "input": "full", "model": "haiku", "provider": "anthropic",
        }}),
    ],
)
def test_no_marker_legacy_keeps_cloud_first_behaviour(attempt: int, expected: Any) -> None:
    before = make_workflow_no_marker()
    after = make_workflow_no_marker()
    patcher.patch(after)
    before_out = run_node(code_of(before, WORKER), stubs(LEGACY_CONFIG, attempt))
    after_out = run_node(code_of(after, WORKER), stubs(LEGACY_CONFIG, attempt))
    assert before_out == expected
    assert after_out == before_out


@pytest.mark.skipif(not NODE_AVAILABLE, reason="node is not on PATH")
@pytest.mark.parametrize(
    ("attempt", "expected"),
    [
        (1, {"json": {
            "session_id": "sess-1", "input": "full", "model": "haiku", "provider": "anthropic",
        }}),
        (2, {"json": {
            "session_id": "", "input": "full", "model": "qwen", "provider": "local",
        }}),
    ],
)
def test_no_marker_roles_switch_to_local_on_attempt_two(attempt: int, expected: Any) -> None:
    w = make_workflow_no_marker()
    patcher.patch(w)
    out = run_node(code_of(w, WORKER), stubs(CLOUD_CONFIG, attempt))
    assert out == expected


@pytest.mark.skipif(not NODE_AVAILABLE, reason="node is not on PATH")
def test_marker_legacy_resolves_defined_worker_model() -> None:
    w = make_workflow()
    patcher.patch(w)
    out = run_node(code_of(w, WORKER), stubs(LEGACY_CONFIG, 1))
    assert out == [{"json": {
        "session_id": "sess-1", "input": "full", "model": "haiku", "provider": "anthropic",
    }}]
