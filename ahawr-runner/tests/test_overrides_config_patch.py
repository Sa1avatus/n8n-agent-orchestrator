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

PATCH_PATH = (
    Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "overrides_config_patch.py"
)
NODE_AVAILABLE = shutil.which("node") is not None
NL = "\n"

HARNESS = """
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = new Function('row', 'mission', input.code)(input.row, input.mission);
process.stdout.write(JSON.stringify(out));
"""

OPTNUM = (
    "const optNum = (value, fallback, min) => (Number(value) >= min ? Number(value) : fallback);"
)
CORPORA_LINE = "const parseCorpora = (text) => (text ? JSON.parse(text) : []);"
ROW = {
    "retrieval_enabled": "true",
    "retrieval_url": " http://retrieval.test:8500/ ",
    "retrieval_timeout_ms": 45000,
    "retrieval_worker_max_tokens": 300,
    "retrieval_reviewer_max_tokens": 900,
    "retrieval_label": " docs ",
    "retrieval_corpora_json": '[{"name":"docs"}]',
}

def load_patch() -> ModuleType:
    spec = importlib.util.spec_from_file_location("overrides_config_patch", PATCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


patcher = load_patch()

ANCHORS = [(patcher.CORPORA_OLD, patcher.CORPORA_NEW), *patcher.REPLACEMENTS]


def constants_code() -> str:
    lines = [
        OPTNUM,
        CORPORA_LINE,
        patcher.CORPORA_OLD,
        patcher.REPLACEMENTS[0][0],
        patcher.REPLACEMENTS[1][0],
        "return [{ json: {",
        "  retrieval_corpora: corpusEntries,",
        *(old for old, _ in patcher.REPLACEMENTS[2:]),
        "} }];",
    ]
    return NL.join(lines)


def make_workflow(code: str | None = None) -> dict[str, Any]:
    return {
        "nodes": [
            {"name": patcher.CONSTANTS, "parameters": {"jsCode": code or constants_code()}},
        ]
    }


def jscode(w: dict[str, Any]) -> str:
    return str(w["nodes"][0]["parameters"]["jsCode"])


def run_constants(code: str, overrides_json: str | None) -> Any:
    payload = json.dumps({
        "code": code,
        "row": ROW,
        "mission": {"overrides_json": overrides_json},
    })
    done = subprocess.run(
        ["node", "-e", HARNESS],
        input=payload,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(done.stdout)[0]["json"]


def test_each_anchor_becomes_exactly_one_replacement() -> None:
    w = make_workflow()
    assert patcher.patch(w) is True
    code = jscode(w)
    for old, new in ANCHORS:
        assert code.count(new) == 1
        assert code.count(old) == 0


def test_second_apply_changes_nothing() -> None:
    w = make_workflow()
    assert patcher.patch(w) is True
    patched = make_workflow(jscode(w))
    assert patcher.patch(w) is False
    assert w == patched


def test_missing_anchor_raises_and_writes_nothing() -> None:
    before = constants_code().replace(patcher.REPLACEMENTS[-1][0], "")
    w = make_workflow(before)
    with pytest.raises(SystemExit, match=re.escape(f"{patcher.CONSTANTS}: anchor found 0 times")):
        patcher.patch(w)
    assert jscode(w) == before


@pytest.mark.skipif(not NODE_AVAILABLE, reason="node is not on PATH")
@pytest.mark.parametrize("overrides_json", ["", "{}", None])
def test_empty_overrides_leave_constants_unchanged(overrides_json: str | None) -> None:
    before = constants_code()
    w = make_workflow()
    patcher.patch(w)
    assert run_constants(jscode(w), overrides_json) == run_constants(before, overrides_json)


def test_mission_node_hands_overrides_json_on_once() -> None:
    mission_code = NL.join(["return [{ json: {", patcher.MISSION_OLD])
    constants = {"name": "Constants", "parameters": {"jsCode": constants_code()}}
    workflow: dict[str, Any] = {
        "nodes": [{"name": "Mission", "parameters": {"jsCode": mission_code}}, constants]
    }
    assert patcher.patch(workflow) is True
    handed_on = workflow["nodes"][0]["parameters"]["jsCode"]
    assert "overrides_json: String(row.overrides_json" in handed_on
    assert patcher.patch(workflow) is False
    assert workflow["nodes"][0]["parameters"]["jsCode"] == handed_on
