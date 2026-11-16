"""Tests of data-tables/ops/fallback_option_patch.py: `options.fallback` of a role.

The patch is idempotent and refuses a missing anchor naming the node. The inserted block runs
standalone in node: a cloud run with a fallback goes to it, a run without the option or with the
same model gets none.
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
PATCH_PATH = OPS_DIR / "fallback_option_patch.py"

needs_patch = pytest.mark.skipif(
    not PATCH_PATH.is_file(), reason=f"ops patch missing: {PATCH_PATH}"
)
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

WORKER_CODE = (
    "const __failover = (() => {\n"
    "  if (__report || __run.provider === 'local') return {};\n"
    "  return { next: true };\n"
    "})();\n"
)
REVIEWER_CODE = (
    "const __reviewerFailover = (() => {\n"
    "  if (__cfg.provider === 'local') return {};\n"
    "  return { next: true };\n"
    "})();\n"
)


def load_patch() -> ModuleType:
    spec = importlib.util.spec_from_file_location("fallback_option_patch", PATCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow() -> dict[str, list[dict[str, object]]]:
    return {
        "nodes": [
            {"name": "Build Worker Run Input", "parameters": {"jsCode": WORKER_CODE}},
            {"name": "Build Reviewer Run Input", "parameters": {"jsCode": REVIEWER_CODE}},
        ]
    }


@needs_patch
def test_fallback_option_patch_is_idempotent() -> None:
    patch = load_patch()
    flow = workflow()
    assert patch.patch(flow) is True
    once = json.dumps(flow, sort_keys=True)
    assert patch.patch(flow) is False
    assert json.dumps(flow, sort_keys=True) == once
    assert once.count("fallback_option_patch.py") == 2


@needs_patch
def test_fallback_option_patch_names_the_node_with_a_missing_anchor() -> None:
    patch = load_patch()
    flow = workflow()
    flow["nodes"][0]["parameters"]["jsCode"] = "const __failover = 1;\n"  # type: ignore[index]
    with pytest.raises(SystemExit) as caught:
        patch.patch(flow)
    assert "Build Worker Run Input" in str(caught.value)


@needs_patch
@needs_node
@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"fallback": "strata/qwen3.8-flash-next-q2_0"}, "strata|qwen3.8-flash-next-q2_0"),
        ({"fallback": "openrouter/a/b:free"}, "openrouter|a/b:free"),
        ({}, "none"),
        ({"fallback": "anthropic/claude-sonnet-5-5"}, "none"),
        ({"fallback": "qwen"}, "none"),
    ],
)
def test_fallback_option_block_picks_the_named_model(
    tmp_path: Path, options: dict[str, str], expected: str
) -> None:
    patch = load_patch()
    block = patch.EDITS["Build Worker Run Input"][1]
    script = (
        "const __run = {provider: 'anthropic', model: 'claude-sonnet-5-5', options: "
        + json.dumps(options)
        + "};\nconst f = (() => {\n"
        + block
        + "  return {};\n})();\n"
        "console.log(\n"
        "  f.fallback_provider ? f.fallback_provider + '|' + f.fallback_model : 'none'\n"
        ");\n"
    )
    path = tmp_path / "block.js"
    path.write_text(script, encoding="utf-8")
    done = subprocess.run(["node", str(path)], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == expected
