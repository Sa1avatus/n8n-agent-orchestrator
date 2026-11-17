"""verdict_retry_fallback_patch.py (operator script): the re-ask keeps the answering model."""
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
SCRIPT = OPS_DIR / "verdict_retry_fallback_patch.py"
SOURCE = Path(__file__).parent / "fixtures" / "prepare_verdict_retry_live.js"
needs_scripts = pytest.mark.skipif(not SCRIPT.is_file(), reason="operator script not found")
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
NODE = "Prepare Verdict Retry"


def _module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow() -> dict[str, Any]:
    prepare = SOURCE.read_text(encoding="utf-8")
    return {"nodes": [{"name": NODE, "parameters": {"jsCode": prepare}}]}


def _code(workflow: dict[str, Any]) -> str:
    code: str = workflow["nodes"][0]["parameters"]["jsCode"]
    return code


@needs_scripts
def test_patch_changes_the_node_once_and_is_idempotent() -> None:
    workflow = _workflow()
    module = _module(SCRIPT, "verdict_retry_fallback_patch")
    assert module.patch(workflow) == [NODE]
    assert "__answered.model" in _code(workflow)
    before = json.dumps(workflow, sort_keys=True)
    assert module.patch(workflow) == []
    assert json.dumps(workflow, sort_keys=True) == before


@needs_scripts
def test_patch_refuses_a_node_without_the_anchor() -> None:
    workflow = _workflow()
    workflow["nodes"][0]["parameters"]["jsCode"] = "return [];"
    with pytest.raises(SystemExit, match="anchor occurs 0 times"):
        _module(SCRIPT, "verdict_retry_fallback_patch").patch(workflow)


def _run_node(tmp_path: Path, code: str, failed_over_from: str) -> dict[str, Any]:
    code_file = tmp_path / "code.js"
    code_file.write_text(code, encoding="utf-8")
    harness = Path(__file__).parent / "fixtures" / "prepare_verdict_retry_harness.js"
    done = subprocess.run(
        ["node", str(harness), str(code_file), failed_over_from],
        capture_output=True, text=True, check=True,
    )
    result: dict[str, Any] = json.loads(done.stdout)
    return result


@needs_scripts
@needs_node
def test_reask_goes_to_the_answering_model_with_the_rest_as_fallbacks(tmp_path: Path) -> None:
    workflow = _workflow()
    _module(SCRIPT, "verdict_retry_fallback_patch").patch(workflow)
    code = _code(workflow)
    primary = _run_node(tmp_path, code, "")
    assert primary == {
        "provider": "anthropic", "model": "opus", "fp": "strata", "fm": "qwen",
        "chain": [{"provider": "openrouter", "model": "free"}],
    }
    fallback = _run_node(tmp_path, code, "anthropic/opus")
    assert fallback == {
        "provider": "strata", "model": "qwen", "fp": "openrouter", "fm": "free", "chain": [],
    }
    second = _run_node(tmp_path, code, "anthropic/opus > strata/qwen")
    assert second == {"provider": "openrouter", "model": "free", "fp": "", "fm": "", "chain": []}
