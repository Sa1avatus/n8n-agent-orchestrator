from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.test_ops_patches_qa import _launch_steps, _live_workflow, _run_steps, _sha256

OPS_DIR = Path(__file__).resolve().parents[2] / "data-tables" / "ops"
FRESH_EXPORT = OPS_DIR / "_scratch" / "t001a" / "live_export.json"
ROLES_CONFIG_SCRIPT = "roles_config_patch"
ROLES_OPTIONS_SCRIPT = "roles_options_patch"
ROLES_OPTIONS_STEP = (ROLES_OPTIONS_SCRIPT, '"$SP/wf_next.json"')
LEGACY_SCRIPT = "legacy_columns_optional_patch"
RETRIEVAL_SCRIPT = "retrieval_config_patch"
RETRIEVAL_STEP = (RETRIEVAL_SCRIPT, '"$SP/wf_next.json"')
CONSTANTS_NODE = "Constants"
ROLES_OPTIONS_MARK = "const optionOf = "
needs_fresh_export = pytest.mark.skipif(
    not FRESH_EXPORT.is_file(), reason=f"fresh export not found: {FRESH_EXPORT}"
)


def _steps_in_launch_order() -> list[tuple[str, str]]:
    # legacy_columns_optional_patch rewrites the same Constants reads as roles_options_patch: the
    # two are alternatives, and the launch script runs the legacy one, so it is left out here
    steps = [step for step in _launch_steps() if step[0] != LEGACY_SCRIPT]
    position = [script for script, _ in steps].index(ROLES_CONFIG_SCRIPT) + 1
    return [*steps[:position], ROLES_OPTIONS_STEP, RETRIEVAL_STEP, *steps[position:]]


def _constants_code(workflow: Path) -> str:
    node = next(n for n in _live_workflow(workflow)["nodes"] if n["name"] == CONSTANTS_NODE)
    code: str = node["parameters"]["jsCode"]
    return code


@needs_fresh_export
def test_all_launch_patches_twice_and_roles_options_is_idempotent(tmp_path: Path) -> None:
    source_sha256 = _sha256(FRESH_EXPORT)
    workflow = tmp_path / "live_export.json"
    shutil.copyfile(FRESH_EXPORT, workflow)
    steps = _steps_in_launch_order()

    for step in steps:
        _run_steps([step], workflow, "final")
    assert ROLES_OPTIONS_MARK in _constants_code(workflow)

    for step in steps:
        before_sha256 = _sha256(workflow)
        _run_steps([step], workflow, "final")
        if step[0] in (ROLES_OPTIONS_SCRIPT, RETRIEVAL_SCRIPT):
            assert _sha256(workflow) == before_sha256

    assert _sha256(FRESH_EXPORT) == source_sha256
