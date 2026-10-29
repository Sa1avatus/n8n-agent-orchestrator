"""failover_patch.py (data-tables/ops, not in git): anchors, idempotence and the patched Retry
Controller run with node: after a few refused tries the same task goes to the model of the next
attempt, once, in a fresh session with the full task text."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PATCH_PATH = Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "failover_patch.py"
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not PATCH_PATH.exists(),
    reason="node or the ops patch file is not available",
)

RETRY_CODE = "\n".join([
    "const retries=Number($json.retry_count??0);",
    "const max=Number($json.max_retries??3);",
    "const delays=Array.isArray($json.retry_delays)?$json.retry_delays: [15,30,60,120,300];",
    "if(retries>=max) return [{json:{...$json,retry_exhausted:true,route:'provider_unavailable',",
    "final_status:'failed',failure_reason:`Unavailable after ${max} retries.`}}];",
    "const next=retries+1;",
    "const wait=Number(delays[Math.min(next-1,delays.length-1)]??30);",
    "return [{json:{...$json,retry_count:next,retry_delay_seconds:wait,retry_exhausted:false}}];",
])
NORMALIZE_CODE = "\n".join([
    "const state = {",
    "  model: String(i.model ?? '').trim(),",
    "  provider: String(i.provider ?? '').trim(),",
    "  service_model: String(i.service_model ?? i.model ?? '').trim(),",
    "};",
])
RESULT_CODE = (
    "return [{ json: { role: 'x', "
    "handoff_from_session_id: String($json.handoff_from_session_id ?? '') } }];"
)
WORKER_CODE = "\n".join([
    "const __run = __report ? __report.cfg : __cfg;",
    "return { json: {",
    "  provider: __run.provider,",
    "  poll_seconds: 3",
    "} };",
])


RESTORE_NAMES = (
    "Restore Runner State — Run Context",
    "Restore Runner State — Status",
)
RESTORE_CODE = "\n".join([
    "const persisted = $json ?? {};",
    "const runner = $('Evaluate Status').last().json ?? {};",
    "return [{ json: { ...persisted, model: String(runner.model ?? ''), "
    "handoff_from_session_id: String(runner.handoff_from_session_id ?? '') } }];",
])


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("failover_patch", PATCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow() -> dict[str, Any]:
    def code_node(name: str, code: str) -> dict[str, Any]:
        return {"name": name, "parameters": {"jsCode": code}}

    return {
        "nodes": [
            code_node("Build Worker Run Input", WORKER_CODE),
            code_node("Normalize Request", NORMALIZE_CODE),
            code_node("Retry Controller", RETRY_CODE),
            code_node("Build Result", RESULT_CODE),
            *[code_node(name, RESTORE_CODE) for name in RESTORE_NAMES],
        ]
    }


def run_controller(code: str, state: dict[str, Any]) -> dict[str, Any]:
    script = (
        "const input = JSON.parse(process.argv[1]);"
        "const f = new Function('$json', require('fs').readFileSync(0, 'utf8'));"
        "process.stdout.write(JSON.stringify(f(input)[0].json));"
    )
    done = subprocess.run(
        ["node", "-e", script, json.dumps(state)],
        input=code,
        capture_output=True,
        text=True,
        check=True,
    )
    return dict(json.loads(done.stdout))


BASE = {
    "provider": "openrouter", "model": "free-model",
    "fallback_provider": "local", "fallback_model": "qwen",
    "input": "resume text", "full_input": "FULL TASK",
    "session_id": "abc", "requested_session_id": "abc",
    "max_retries": 6, "retry_delays": [60, 120, 180],
}


def test_the_patch_changes_the_nodes_once() -> None:
    patch = load()
    wf = workflow()
    assert patch.patch(wf) == [
        "Build Worker Run Input", "Normalize Request", "Retry Controller", "Build Result",
        *RESTORE_NAMES,
    ]
    assert patch.patch(wf) == []
    worker = wf["nodes"][0]["parameters"]["jsCode"]
    assert "...__failover," in worker and "full_input: __input," in worker


def test_a_missing_anchor_is_refused() -> None:
    patch = load()
    wf = workflow()
    wf["nodes"][2]["parameters"]["jsCode"] = "return [];"
    with pytest.raises(AssertionError, match="Retry Controller"):
        patch.patch(wf)


def test_the_controller_waits_twice_then_fails_over_once() -> None:
    patch = load()
    wf = workflow()
    patch.patch(wf)
    code = wf["nodes"][2]["parameters"]["jsCode"]
    first = run_controller(code, {**BASE, "retry_count": 0})
    got = (first["retry_count"], first["retry_delay_seconds"], first["model"])
    assert got == (1, 60, "free-model")
    second = run_controller(code, {**BASE, "retry_count": 1})
    assert (second["retry_count"], second["model"]) == (2, "free-model")
    over = run_controller(code, {**BASE, "retry_count": 2})
    assert (over["provider"], over["model"], over["input"]) == ("local", "qwen", "FULL TASK")
    assert over["session_id"] == "" and over["requested_session_id"] == ""
    assert (over["retry_count"], over["retry_delay_seconds"]) == (0, 0)
    assert over["failed_over_from"] == "openrouter/free-model"


def test_after_the_failover_the_old_limits_apply_and_there_is_no_second_one() -> None:
    patch = load()
    wf = workflow()
    patch.patch(wf)
    code = wf["nodes"][2]["parameters"]["jsCode"]
    over = run_controller(code, {**BASE, "retry_count": 2})
    again = run_controller(code, {**over, "retry_count": 2})
    assert (again["model"], again["retry_count"]) == ("qwen", 3)
    done = run_controller(code, {**over, "retry_count": 6})
    assert done["retry_exhausted"] is True and done["route"] == "provider_unavailable"


def test_without_a_fallback_the_behaviour_is_unchanged() -> None:
    patch = load()
    wf = workflow()
    patch.patch(wf)
    code = wf["nodes"][2]["parameters"]["jsCode"]
    state = {**BASE, "fallback_model": "", "fallback_provider": ""}
    assert run_controller(code, {**state, "retry_count": 2})["model"] == "free-model"
    assert run_controller(code, {**state, "retry_count": 6})["retry_exhausted"] is True


def test_a_poll_rebuild_of_the_state_keeps_the_fallback_fields() -> None:
    patch = load()
    wf = workflow()
    patch.patch(wf)
    code = wf["nodes"][4]["parameters"]["jsCode"]
    script = (
        "const runner = JSON.parse(process.argv[1]);"
        "const $ = () => ({ last: () => ({ json: runner }) });"
        "const f = new Function('$json', '$', require('fs').readFileSync(0, 'utf8'));"
        "process.stdout.write(JSON.stringify(f({ retry_count: 2 }, $)[0].json));"
    )
    done = subprocess.run(
        ["node", "-e", script, json.dumps(BASE)],
        input=code, capture_output=True, text=True, check=True,
    )
    kept = json.loads(done.stdout)
    assert (kept["fallback_provider"], kept["fallback_model"]) == ("local", "qwen")
    assert kept["full_input"] == "FULL TASK" and kept["failed_over_from"] == ""
