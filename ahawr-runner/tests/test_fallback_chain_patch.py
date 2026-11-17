"""fallback_chain_patch.py (operator script): a role may list several fallbacks."""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

OPS_DIR = Path(__file__).resolve().parents[2] / "data-tables" / "ops"
SCRIPT = OPS_DIR / "fallback_chain_patch.py"
needs_script = pytest.mark.skipif(not SCRIPT.is_file(), reason="script not found")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

NORMALIZE_CODE = "return {\n  failed_over_from: '',\n  service_model: 'x',\n};"
PARSE = "const text = String((%s.options ?? {}).fallback ?? '').trim();\n"
RETRY_CODE = "\n".join([
    "const retries=Number($json.retry_count??0);",
    "const max=Number($json.max_retries??3);",
    "const failoverAfter=Math.min(max,2);",
    "if(retries>=max) return [{json:{...$json,retry_exhausted:true,route:'provider_unavailable',",
    "final_status:'failed'}}];",
    "return [{json:{...$json,retry_count:retries+1}}];",
])


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fallback_chain_patch", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow() -> dict[str, Any]:
    def build(cfg: str) -> str:
        return PARSE % cfg + f"return {{\n  provider: {cfg}.provider,\n  model: {cfg}.model,\n}};\n"

    return {"nodes": [
        {"name": "Build Worker Run Input", "parameters": {"jsCode": build("__run")}},
        {"name": "Build Reviewer Run Input", "parameters": {"jsCode": build("__cfg")}},
        {"name": "Build Architect Run Input", "parameters": {"jsCode": build("__cfg")}},
        {"name": "Normalize Request", "parameters": {"jsCode": NORMALIZE_CODE}},
        {"name": "Retry Controller", "parameters": {"jsCode": RETRY_CODE}},
        {"name": "Restore Runner State — Run Context", "parameters": {"jsCode": (
            "return { json: { failed_over_from: String(runner.failed_over_from ?? '') } }];")}},
        {"name": "Restore Runner State — Status", "parameters": {"jsCode": (
            "return { json: { failed_over_from: String(runner.failed_over_from ?? '') } }];")}},
    ]}


def _code(workflow: dict[str, Any], name: str) -> str:
    node = next(n for n in workflow["nodes"] if n["name"] == name)
    code: str = node["parameters"]["jsCode"]
    return code


@needs_script
def test_patch_reaches_the_three_run_inputs_and_the_run_manager() -> None:
    workflow = _workflow()
    done = _module().patch(workflow)
    assert len(done) == 7
    for name in ("Build Worker Run Input", "Build Reviewer Run Input", "Build Architect Run Input"):
        code = _code(workflow, name)
        assert "[].concat(" in code and "fallback_chain:" in code
    assert "fallback_chain: Array.isArray(i.fallback_chain)" in _code(workflow, "Normalize Request")
    assert "__chain.slice(1)" in _code(workflow, "Retry Controller")


@needs_script
def test_patch_is_idempotent() -> None:
    workflow = _workflow()
    module = _module()
    module.patch(workflow)
    before = json.dumps(workflow, sort_keys=True)
    assert module.patch(workflow) == []
    assert json.dumps(workflow, sort_keys=True) == before


@needs_script
def test_patch_refuses_a_workflow_without_the_fallback_option_code() -> None:
    workflow = _workflow()
    workflow["nodes"][0]["parameters"]["jsCode"] = "return { provider: __run.provider,\n};\n"
    with pytest.raises(SystemExit, match="fallback option is not read yet"):
        _module().patch(workflow)


@needs_script
@needs_node
def test_retry_controller_walks_the_chain_then_gives_up(tmp_path: Path) -> None:
    workflow = _workflow()
    _module().patch(workflow)
    body = _code(workflow, "Retry Controller")
    script = tmp_path / "walk.js"
    script.write_text(
        "const step = ($json) => { " + body + " };\n"
        "let s = {retry_count: 2, max_retries: 3, provider: 'a', model: 'm',\n"
        "  failed_over_from: 'a/first', full_input: 'T', input: 'T',\n"
        "  fallback_chain: [{provider: 'b', model: 'n'}, {provider: 'c', model: 'o'}]};\n"
        "const seen = [];\n"
        "for (let i = 0; i < 3; i++) {\n"
        "  s = step({...s, retry_count: 2})[0].json;\n"
        "  seen.push(s.provider + '/' + s.model);\n"
        "}\n"
        "s = step({...s, retry_count: 3})[0].json;\n"
        "const left = s.fallback_chain.length;\n"
        "console.log(JSON.stringify({seen, exhausted: s.retry_exhausted, left}));\n",
        encoding="utf-8",
    )
    done = subprocess.run(["node", str(script)], capture_output=True, text=True, check=True)
    assert json.loads(done.stdout) == {"seen": ["b/n", "c/o", "c/o"], "exhausted": True, "left": 0}
