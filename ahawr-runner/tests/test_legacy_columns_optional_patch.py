from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PATCH_PATH = (
    Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "legacy_columns_optional_patch.py"
)
NODE_AVAILABLE = shutil.which("node") is not None
ROLE_READS = [
    ("architect_max_provider_retries", "architect_max_retries", 0),
    ("worker_max_provider_retries", "worker_max_retries", 0),
    ("reviewer_max_provider_retries", "reviewer_max_retries", 0),
    ("architect_max_polls", "architect_max_polls", 1),
    ("worker_max_polls", "worker_max_polls", 1),
    ("reviewer_max_polls", "reviewer_max_polls", 1),
    ("architect_poll_seconds", "architect_poll_seconds", 1),
    ("worker_poll_seconds", "worker_poll_seconds", 1),
    ("reviewer_poll_seconds", "reviewer_poll_seconds", 1),
]


def _role_read_line(key: str, column: str, minimum: int) -> str:
    return f"  {key}: num(row.{column},'{key}',{minimum}),"


MISSING_ANCHOR_LINE = _role_read_line("worker_max_polls", "worker_max_polls", 1)
ROLE_PAIR_READS = [
    ("planner_provider", "architect_provider"),
    ("planner_model", "architect_model"),
    ("worker_provider", "worker_provider"),
    ("worker_model", "worker_model"),
    ("reviewer_provider", "reviewer_provider"),
    ("reviewer_model", "reviewer_model"),
    ("service_provider", "service_provider"),
    ("service_model", "service_model"),
]
CONSTANTS_LINES = [
    "function num(value, field, min = 0) { return Number(value); }",
    "const row = {};",
    "const mission = {};",
    "return [{ json: {",
    "  max_tasks: num(row.max_tasks,'max_tasks',1),",
    *[f"  {key}: String(row.{column} ?? '').trim()," for key, column in ROLE_PAIR_READS],
    *[_role_read_line(*read) for read in ROLE_READS],
    "} }];",
]
SYNTHETIC_CONSTANTS = "\n".join(CONSTANTS_LINES)


@pytest.fixture(scope="module")
def patch_module() -> ModuleType:
    if not PATCH_PATH.is_file():
        pytest.skip(f"patch not found: {PATCH_PATH}")
    spec = importlib.util.spec_from_file_location("legacy_columns_optional_patch", PATCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow(code: str) -> dict[str, Any]:
    return {"nodes": [{"name": "Constants", "parameters": {"jsCode": code}}]}


def _code(workflow: dict[str, Any]) -> str:
    code: str = workflow["nodes"][0]["parameters"]["jsCode"]
    return code


def test_second_run_leaves_constants_unchanged(patch_module: ModuleType) -> None:
    workflow = _workflow(SYNTHETIC_CONSTANTS)
    assert patch_module.patch(workflow) is True
    patched = _code(workflow)
    assert patched.count("legacyOr(") == 11
    assert patch_module.patch(workflow) is False
    assert _code(workflow) == patched


def test_missing_anchor_stops_without_writing(patch_module: ModuleType) -> None:
    code = SYNTHETIC_CONSTANTS.replace(MISSING_ANCHOR_LINE + "\n", "")
    workflow = _workflow(code)
    with pytest.raises(SystemExit, match="anchor found 0 times"):
        patch_module.patch(workflow)
    assert _code(workflow) == code


@pytest.mark.skipif(not NODE_AVAILABLE, reason="node is not on PATH")
def test_patched_constants_passes_node_check(patch_module: ModuleType, tmp_path: Path) -> None:
    workflow = _workflow(SYNTHETIC_CONSTANTS)
    patch_module.patch(workflow)
    script = tmp_path / "constants.js"
    script.write_text(_code(workflow), encoding="utf-8")
    result = subprocess.run(
        ["node", "--check", str(script)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
