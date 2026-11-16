"""Tests of data-tables/ops/architect_fallback_patch.py: the Architect names its fallback model.

The patch is idempotent and refuses a missing anchor naming the node. The inserted block runs
standalone in node: `options.fallback` wins, else the model of attempt 2, else nothing.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

OPS_DIR = Path(__file__).resolve().parents[2] / "data-tables" / "ops"
PATCH_PATH = OPS_DIR / "architect_fallback_patch.py"

needs_patch = pytest.mark.skipif(
    not PATCH_PATH.is_file(), reason=f"ops patch missing: {PATCH_PATH}"
)
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

ARCHITECT_CODE = (
    "const __cfg = __roleConfig('architect', 1);\n"
    "return { json: {\n"
    "  model: __cfg.model,\n"
    "  provider: __cfg.provider,\n"
    "  poll_seconds: 1,\n"
    "} };\n"
)


def load_patch() -> ModuleType:
    spec = importlib.util.spec_from_file_location("architect_fallback_patch", PATCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow(code: str = ARCHITECT_CODE) -> dict[str, list[dict[str, object]]]:
    return {"nodes": [{"name": "Build Architect Run Input", "parameters": {"jsCode": code}}]}


@needs_patch
def test_architect_fallback_patch_is_idempotent() -> None:
    patch = load_patch()
    flow = workflow()
    assert patch.patch(flow) is True
    once = json.dumps(flow, sort_keys=True)
    assert patch.patch(flow) is False
    assert json.dumps(flow, sort_keys=True) == once
    assert once.count("...__architectFailover") == 1


@needs_patch
def test_architect_fallback_patch_names_the_missing_anchor() -> None:
    patch = load_patch()
    with pytest.raises(SystemExit) as caught:
        patch.patch(workflow("const __cfg = __roleConfig('architect', 1);\n"))
    assert "provider: __cfg.provider" in str(caught.value)


@needs_patch
@needs_node
@pytest.mark.parametrize(
    ("options", "second", "expected"),
    [
        ({"fallback": "anthropic/claude-opus-5-5"}, "anthropic|claude-fable-5-2",
         "anthropic|claude-opus-5-5"),
        ({"fallback": "openrouter/a/b:free"}, "anthropic|claude-fable-5-2", "openrouter|a/b:free"),
        ({}, "anthropic|claude-sonnet-5-5", "anthropic|claude-sonnet-5-5"),
        ({}, "anthropic|claude-fable-5-2", "none"),
        ({"fallback": "anthropic/claude-fable-5-2"}, "anthropic|claude-fable-5-2", "none"),
        ({"fallback": "opus"}, "anthropic|claude-fable-5-2", "none"),
    ],
)
def test_architect_failover_block_picks_the_named_model(
    tmp_path: Path, options: dict[str, str], second: str, expected: str
) -> None:
    patch = load_patch()
    provider, model = second.split("|")
    script = (
        "const __cfg = {provider: 'anthropic', model: 'claude-fable-5-2', options: "
        + json.dumps(options)
        + "};\n"
        f"const __roleConfig = (role, attempt) => attempt === 1 ? __cfg : "
        f"{{provider: '{provider}', model: '{model}', options: {{}}}};\n"
        + patch.CALL_BLOCK
        + "const f = __architectFailover;\n"
        "console.log(\n"
        "  f.fallback_provider ? f.fallback_provider + '|' + f.fallback_model : 'none'\n"
        ");\n"
    )
    path = tmp_path / "block.js"
    path.write_text(script, encoding="utf-8")
    done = subprocess.run(["node", str(path)], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == expected
