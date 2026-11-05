"""Ops chain plus legacy_columns_optional_patch, applied twice to the scratch export; then the
Mission node, the Constants node and the Worker role resolution run with node on a legacy row
and on a stripped row. The resolved values must be the same.

Run: python -m pytest tests/test_all_patches_constants_roles.py -q --no-cov
"""

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
OPS_TEST_PATH = OPS_DIR / "test_all_patches_order.py"
# launch_mission.sh.new, the chain the ops all-patches test reads, has none of the three JSON
# readers below yet (T003D registers them); without them the JSON values would never be read.
WF_NEXT = '"$SP/wf_next.json"'
JSON_READER_STEPS = [
    ("thinking_config_patch", WF_NEXT),
    ("retrieval_config_patch", WF_NEXT),
    ("overrides_config_patch", WF_NEXT),
]
LEGACY_STEP = ("legacy_columns_optional_patch", "")
MISSION_NODE = "Mission"
CONSTANTS_NODE = "Constants"
ROLE_NODE = "Build Worker Run Input"
ROLE_SECTION_END = "\nconst fromReport ="
RUNNER_TIMEOUT_SECONDS = 30
MISSING = object()

ROLE_TAIL = """
const resolved = {};
for (const role of ["architect", "worker", "reviewer"]) {
  for (const attempt of [1, 2]) resolved[`${role}.${attempt}`] = __roleConfig(role, attempt);
}
return resolved;
"""

RUNNER_JS = """
const fs = require('fs');
const dir = process.argv[2];
const read = (name) => fs.readFileSync(`${dir}/${name}`, 'utf8');
const input = JSON.parse(read('input.json'));
const lookup = { 'Load Config': { all: () => input.hermes_rows.map((json) => ({ json })) } };
const $ = (name) => lookup[name];
const mission = new Function('$', '$json', read('mission.js'))($, input.mission_row)[0].json;
lookup.Mission = { first: () => ({ json: mission }) };
const constants = new Function('$', read('constants.js'))($)[0].json;
lookup.Constants = { first: () => ({ json: constants }) };
const roles = new Function('$', read('role.js'))($);
process.stdout.write(JSON.stringify({ mission, constants, roles }));
"""

RUNNER_URL = "http://runner.example.test:8700"
RUN_MANAGER_WORKFLOW_ID = "wf-test-run-manager-01"
TELEGRAM_CHAT_ID = "1000000001"
RETRY_DELAYS = [15, 30, 60, 120, 300]
RETRIEVAL_URL = "http://retrieval.example.test:8501"
WORKING_DIRECTORY = "/d/example-project-01"
CORPUS_ID = "d-example-project-01"

MISSION_COMMON = {
    "mission_id": "mission-test-01",
    "enabled": True,
    "name": "Mission test 01",
    "version": 1,
    "objective": "Made-up objective for the resolution test.",
    "state_namespace": "ns-test-01",
    "rules_json": "",
    "acceptance_criteria_json": "",
}
# The mission override wins over the settings row: the label differs from retrieval_label there.
MISSION_LABEL = "label-mission"
MISSION_OVERRIDES_JSON = json.dumps(
    {"retrieval": {"corpora": [CORPUS_ID], "label": MISSION_LABEL}}
)
LEGACY_MISSION_ROW = {
    **MISSION_COMMON,
    "retrieval_corpora_json": json.dumps([CORPUS_ID]),
    "workspace": WORKING_DIRECTORY,
    "overrides_json": MISSION_OVERRIDES_JSON,
}
STRIPPED_MISSION_ROW = {
    **MISSION_COMMON,
    "working_directory": WORKING_DIRECTORY,
    "overrides_json": MISSION_OVERRIDES_JSON,
}
LEGACY_MISSION_FIELDS = {"workspace", "work_dir", "workdir", "project_dir", "path"}

SHARED_ROW = {"profile_id": "claude-code", "enabled": True, "roles_json": ""}
LEGACY_COLUMNS: dict[str, Any] = {
    "architect_provider": "alpha-provider",
    "architect_model": "model-architect",
    "worker_provider": "beta-provider",
    "worker_model": "model-worker",
    "reviewer_provider": "gamma-provider",
    "reviewer_model": "model-reviewer",
    "service_provider": "delta-provider",
    "service_model": "model-service",
    "max_tasks": 15,
    "max_attempts_per_task": 5,
    "retry_delays_json": json.dumps(RETRY_DELAYS),
    "runner_url": RUNNER_URL,
    "run_manager_workflow_id": RUN_MANAGER_WORKFLOW_ID,
    "telegram_chat_id": TELEGRAM_CHAT_ID,
    "architect_max_polls": 30,
    "worker_max_polls": 30,
    "reviewer_max_polls": 20,
    "architect_poll_seconds": 3,
    "worker_poll_seconds": 3,
    "reviewer_poll_seconds": 3,
    "architect_max_retries": 5,
    "worker_max_retries": 3,
    "reviewer_max_retries": 5,
    "worker_think_policy": "",
    "worker_think_policy_from_task": 2,
    "worker_think_off_after_seconds": 600,
    "worker_think_off_after_steps": 40,
    "retrieval_enabled": True,
    "retrieval_url": RETRIEVAL_URL,
    "retrieval_timeout_ms": 90000,
    "retrieval_worker_max_tokens": 7000,
    "retrieval_reviewer_max_tokens": 4500,
    "retrieval_label": "label-test",
    "retrieval_corpora_json": "",
}
# The JSON columns hold the values the legacy columns above hold (engine_json: the engine keys of
# docs/config-engine.md; thinking_json: docs/config-thinking.md; retrieval_json:
# docs/config-retrieval.md). The values differ from the code defaults, so a reader that is missing
# makes a field differ between the runs; _assert_same_resolved pins the values below.
ENGINE_JSON = json.dumps(
    {
        "max_attempts_per_task": 5,
        "retry_delays": RETRY_DELAYS,
        "runner_url": RUNNER_URL,
        "run_manager_workflow_id": RUN_MANAGER_WORKFLOW_ID,
        "telegram_chat_id": TELEGRAM_CHAT_ID,
    }
)
THINKING_JSON = json.dumps(
    {"from_task": 2, "off_after_seconds": 600, "off_after_steps": 40}
)
RETRIEVAL_JSON = json.dumps(
    {
        "enabled": True,
        "url": RETRIEVAL_URL,
        "timeout_ms": 90000,
        "worker_max_tokens": 7000,
        "reviewer_max_tokens": 4500,
        "label": "label-test",
    }
)
# Polls and retries stay under constants.* (Constants reads them from the legacy columns only), so
# roles_json carries the provider and model that the role code resolves.
ROLES_JSON = json.dumps(
    {
        "architect": {"1": "alpha-provider/model-architect"},
        "worker": {"1": "beta-provider/model-worker"},
        "reviewer": {"1": "gamma-provider/model-reviewer"},
        "service": {"1": "delta-provider/model-service"},
    }
)
# roles_json is the input itself: Constants passes it on, so it differs between the rows by design.
# Every other Constants field, the provider and model columns included, must match.
INPUT_ONLY_FIELDS = {"constants.roles_json"}


def _load_ops_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("test_all_patches_order", OPS_TEST_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ops = _load_ops_module()
LIVE_EXPORT: Path = ops.LIVE_EXPORT

needs_scratch_export = pytest.mark.skipif(
    not LIVE_EXPORT.is_file(), reason=f"scratchpad export not found: {LIVE_EXPORT}"
)
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _role_code(node_code: str) -> str:
    assert node_code.count(ROLE_SECTION_END) == 1, "role section end not found exactly once"
    return node_code[: node_code.index(ROLE_SECTION_END)]


def _flatten(prefix: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {prefix: value}
    flat: dict[str, Any] = {}
    for key, item in value.items():
        flat.update(_flatten(f"{prefix}.{key}", item))
    return flat


def _run_node(
    workdir: Path,
    runner: Path,
    code: dict[str, str],
    hermes_row: dict[str, Any],
    mission_row: dict[str, Any],
) -> dict[str, Any]:
    workdir.mkdir()
    for name, text in code.items():
        (workdir / f"{name}.js").write_text(text, encoding="utf-8")
    payload = {"hermes_rows": [hermes_row], "mission_row": mission_row}
    (workdir / "input.json").write_text(json.dumps(payload), encoding="utf-8")
    result = subprocess.run(
        ["node", str(runner), str(workdir)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=RUNNER_TIMEOUT_SECONDS,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    resolved: dict[str, Any] = json.loads(result.stdout)
    return {
        **_flatten("mission", resolved["mission"]),
        **_flatten("constants", resolved["constants"]),
        **_flatten("roles", resolved["roles"]),
    }


def _assert_same_resolved(legacy: dict[str, Any], stripped: dict[str, Any]) -> None:
    assert legacy["constants.max_tasks"] == 15 and legacy["roles.worker.1.model"] == "model-worker"
    assert legacy["mission.retrieval_corpora"] == [CORPUS_ID]
    assert legacy["constants.telegram_chat_id"] == TELEGRAM_CHAT_ID
    # values that differ from the code defaults: a reader that is missing shows as a difference
    assert legacy["constants.worker_think_off_after_seconds"] == 600
    assert legacy["constants.retrieval_worker_max_tokens"] == 7000
    assert legacy["constants.retrieval_enabled"] is True
    assert legacy["constants.retrieval_label"] == MISSION_LABEL
    differences = []
    for field in sorted(set(legacy) | set(stripped)):
        left, right = legacy.get(field, MISSING), stripped.get(field, MISSING)
        if field not in INPUT_ONLY_FIELDS and left != right:
            differences.append(f"{field}: legacy {left!r}, stripped {right!r}")
    assert not differences, "; ".join(differences)


@needs_scratch_export
@needs_node
def test_all_patches_twice_then_legacy_and_stripped_rows_resolve_the_same(tmp_path: Path) -> None:
    steps = [*ops._launch_steps(), *JSON_READER_STEPS, LEGACY_STEP]
    workflow = tmp_path / "live_export.json"
    shutil.copyfile(LIVE_EXPORT, workflow)
    ops._run_steps(steps, workflow)
    once_sha256 = ops._sha256(workflow)
    ops._run_steps(steps, workflow)
    assert ops._sha256(workflow) == once_sha256, "second pass changed the workflow"

    nodes = {n["name"]: n for n in ops._nodes(workflow)}
    code = {
        "mission": nodes[MISSION_NODE]["parameters"]["jsCode"],
        "constants": nodes[CONSTANTS_NODE]["parameters"]["jsCode"],
        "role": _role_code(nodes[ROLE_NODE]["parameters"]["jsCode"]) + ROLE_TAIL,
    }
    runner = tmp_path / "runner.js"
    runner.write_text(RUNNER_JS, encoding="utf-8")

    legacy_row = {
        **SHARED_ROW,
        **LEGACY_COLUMNS,
        "engine_json": "",
        "thinking_json": "",
        "retrieval_json": "",
    }
    stripped_row = {
        **SHARED_ROW,
        "roles_json": ROLES_JSON,
        "engine_json": ENGINE_JSON,
        "thinking_json": THINKING_JSON,
        "retrieval_json": RETRIEVAL_JSON,
    }
    assert not set(stripped_row) & set(LEGACY_COLUMNS)
    assert not set(STRIPPED_MISSION_ROW) & LEGACY_MISSION_FIELDS

    legacy = _run_node(tmp_path / "legacy", runner, code, legacy_row, LEGACY_MISSION_ROW)
    stripped = _run_node(tmp_path / "stripped", runner, code, stripped_row, STRIPPED_MISSION_ROW)
    _assert_same_resolved(legacy, stripped)
