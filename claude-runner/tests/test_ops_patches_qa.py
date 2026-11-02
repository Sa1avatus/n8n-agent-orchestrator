from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

OPS_DIR = Path(__file__).resolve().parents[2] / "data-tables" / "ops"
LAUNCH_SCRIPT = OPS_DIR / "launch_mission.sh"
PATCH_PATH = OPS_DIR / "qa_workflow_patch.py"
LIVE_EXPORT = OPS_DIR / "_scratch" / "live_export.json"
QA_SCRIPT = "qa_workflow_patch"
OVERRIDES_SCRIPT = "overrides_config_patch"
MISSION_ID = "t022-test-mission"
PATCH_TIMEOUT_SECONDS = 120
CODE_NODE_TIMEOUT_SECONDS = 30
LAUNCH_PATCH_LINE = re.compile(r'python "\$SP/(?P<script>\w+)\.py"(?P<args>.*)')
needs_live_export = pytest.mark.skipif(
    not LIVE_EXPORT.is_file(), reason=f"live export not found: {LIVE_EXPORT}"
)
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


@pytest.fixture(scope="module")
def qwp() -> ModuleType:
    spec = importlib.util.spec_from_file_location("qa_workflow_patch", PATCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _launch_steps() -> list[tuple[str, str]]:
    steps: list[tuple[str, str]] = []
    for raw_line in LAUNCH_SCRIPT.read_text(encoding="utf-8").splitlines():
        line = re.split(r"\s+#", raw_line, maxsplit=1)[0]
        match = LAUNCH_PATCH_LINE.search(line)
        if match is not None and '"$SP/wf_next.json"' in match["args"]:
            steps.append((match["script"], match["args"]))
    assert steps, "no patch on wf_next.json found in launch_mission.sh"
    assert [script for script, _ in steps][-1] == QA_SCRIPT
    for script, _ in steps:
        assert (OPS_DIR / f"{script}.py").is_file(), script
    return steps


def _adaptive_scratch(workflow: Path) -> Path:
    # the adaptive patch writes only below its scratch folder; the launch script copies it back
    return OPS_DIR / "adaptive_split" / "scratch" / f"qa_test_{workflow.parent.name}.json"


def _script_args(script: str, args: str, workflow: Path, qa_mode: str) -> list[str]:
    if script == QA_SCRIPT:
        return [qa_mode, str(workflow)]
    if "--out" in args:
        return ["--src", str(workflow), "--out", str(_adaptive_scratch(workflow))]
    extra = [MISSION_ID] if '"$MISSION"' in args else []
    return [str(workflow), *extra]


def _run_steps(steps: list[tuple[str, str]], workflow: Path, qa_mode: str) -> None:
    for script, args in steps:
        command = [
            sys.executable,
            "-X",
            "utf8",
            str(OPS_DIR / f"{script}.py"),
            *_script_args(script, args, workflow, qa_mode),
        ]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=PATCH_TIMEOUT_SECONDS,
            check=False,
        )
        assert result.returncode == 0, f"{script} failed: {result.stderr[-2000:]}"
        if "--out" in args:
            scratch = _adaptive_scratch(workflow)
            shutil.move(scratch, workflow)


def _live_workflow(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    workflow: dict[str, Any] = raw[0] if isinstance(raw, list) else raw
    return workflow


def _assert_qa_nodes(qwp: ModuleType, workflow_path: Path) -> None:
    workflow = _live_workflow(workflow_path)
    counts = Counter(node["name"] for node in workflow["nodes"])
    for name in (qwp.QA_REQUEST, qwp.QA_WAIT, qwp.QA_REVIEW, qwp.QA_NOTICE):
        assert counts[name] == 1, name
    assert all(count == 1 for name, count in counts.items() if name.startswith("QA "))
    for old in qwp.OLD_NODES:
        assert counts[old] == 0, old


def _assert_code_nodes_pass_node_check(workflow_path: Path, scratch_dir: Path) -> None:
    code_nodes = [
        n for n in _live_workflow(workflow_path)["nodes"] if n["type"] == "n8n-nodes-base.code"
    ]
    assert code_nodes
    for index, node in enumerate(code_nodes):
        script = scratch_dir / f"code_{index}.js"
        # an n8n code node body runs inside an async function: await is valid at its top level
        body = node["parameters"]["jsCode"]
        script.write_text(f"(async () => {{\n{body}\n}})();", encoding="utf-8")
        result = subprocess.run(
            ["node", "--check", str(script)],
            capture_output=True,
            text=True,
            timeout=CODE_NODE_TIMEOUT_SECONDS,
            check=False,
        )
        assert result.returncode == 0, f"{node['name']}: {result.stderr[-1000:]}"


@needs_live_export
@needs_node
def test_all_launch_patches_twice_on_a_fresh_export(
    qwp: ModuleType, tmp_path: Path
) -> None:
    source_sha256 = _sha256(LIVE_EXPORT)
    workflow = tmp_path / "live_export.json"
    shutil.copyfile(LIVE_EXPORT, workflow)
    steps = _launch_steps()
    steps.insert(-1, (OVERRIDES_SCRIPT, '"$SP/wf_next.json"'))

    _run_steps(steps, workflow, "final")
    once_sha256 = _sha256(workflow)
    _assert_qa_nodes(qwp, workflow)
    _assert_code_nodes_pass_node_check(workflow, tmp_path)

    _run_steps(steps, workflow, "final")
    assert _sha256(workflow) == once_sha256
    assert _sha256(LIVE_EXPORT) == source_sha256


@needs_live_export
def test_mode_off_qa_step_is_a_byte_for_byte_no_op(tmp_path: Path) -> None:
    steps = _launch_steps()
    without_qa = tmp_path / "without_qa.json"
    with_qa = tmp_path / "with_qa.json"
    shutil.copyfile(LIVE_EXPORT, without_qa)
    shutil.copyfile(LIVE_EXPORT, with_qa)

    _run_steps([step for step in steps if step[0] != QA_SCRIPT], without_qa, "off")
    _run_steps(steps, with_qa, "off")
    assert _sha256(with_qa) == _sha256(without_qa)


REVIEW_HARNESS = """
const body = `return (async () => {${process.argv[1]}})();`;
const run = new Function('$', '$input', '$getWorkflowStaticData', body);
const poll = JSON.parse(process.argv[2]);
const reachable = JSON.parse(process.argv[3]);
const last = (_branch, index) => {
  if (index !== undefined && index >= reachable) throw new Error('no such run');
  return { json: poll };
};
const node = (name) => ({
  first: () => ({ json: name === 'Mission' ? { mission_id: 'm1' } : {} }),
  last,
});
run(node, { first: () => ({ json: JSON.parse(process.argv[4]) }) }, () => ({}))
  .then((out) => console.log(JSON.stringify(out[0].json.qa_notice)));
"""


def _review_notice(
    qwp: ModuleType, poll: dict[str, Any], reachable: int, given: dict[str, Any]
) -> str:
    args = [qwp.QA_REVIEW_JS, json.dumps(poll), str(reachable), json.dumps(given)]
    result = subprocess.run(
        ["node", "-e", REVIEW_HARNESS, *args],
        capture_output=True,
        text=True,
        timeout=CODE_NODE_TIMEOUT_SECONDS,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-1000:]
    return str(json.loads(result.stdout))


@needs_node
def test_review_reads_the_finished_poll_after_many_polls(qwp: ModuleType) -> None:
    done = {"status": "done", "verdicts": [{"verdict": "pass"}]}
    # the join hands the finished poll on: the notice must not say the runner is unreachable
    assert _review_notice(qwp, done, 0, done) == "QA passed"
    # the limit branch hands on a counter only: the last poll run is found by its index
    assert _review_notice(qwp, done, 10, {"poll_count": 3}) == "QA passed"


@needs_node
def test_failure_notice_names_scenarios_and_reasons(qwp: ModuleType) -> None:
    failing = [
        {
            "verdict": "fail",
            "layer": "ui",
            "scenario": f"scenario {index}",
            "failed_reason": "Locator timed out",
        }
        for index in range(19)
    ]
    poll = {"status": "done", "verdicts": failing}
    notice = _review_notice(qwp, poll, 0, poll)
    # not "ui, ui, ui, ...": the count, the first scenarios with their reason, the rest as a number
    assert notice.startswith("QA failed (ui 19 of 19 scenarios):")
    assert "- scenario 0: Locator timed out" in notice
    assert "(+16 more)" in notice
    assert "ui, ui" not in notice
