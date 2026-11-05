from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

PATCH_PATH = (
    Path(__file__).resolve().parents[2] / "data-tables" / "ops" / "thinking_config_patch.py"
)
NL = "\n"


def load_patch() -> ModuleType:
    spec = importlib.util.spec_from_file_location("thinking_config_patch", PATCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


patcher = load_patch()

ANCHORS = [
    (patcher.POLICY_OLD, patcher.POLICY_NEW),
    (patcher.FROM_TASK_OLD, patcher.FROM_TASK_NEW),
    (patcher.OFF_SECONDS_OLD, patcher.OFF_SECONDS_NEW),
    (patcher.OFF_STEPS_OLD, patcher.OFF_STEPS_NEW),
]


def constants_code() -> str:
    lines = [
        "const row = $('Trigger').first().json;",
        patcher.RETURN_OLD,
        patcher.POLICY_OLD,
        patcher.FROM_TASK_OLD,
        patcher.OFF_SECONDS_OLD,
        patcher.OFF_STEPS_OLD,
        "}}];",
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


def test_first_apply_patches_and_second_apply_changes_nothing() -> None:
    w = make_workflow()
    assert patcher.patch(w) is True
    patched = make_workflow(jscode(w))
    assert patcher.patch(w) is False
    assert w == patched


@pytest.mark.parametrize(("old", "new"), ANCHORS)
def test_each_anchor_becomes_exactly_one_replacement(old: str, new: str) -> None:
    w = make_workflow()
    patcher.patch(w)
    code = jscode(w)
    assert code.count(new) == 1
    assert code.count(old) == 0


def test_missing_anchor_raises_and_writes_nothing() -> None:
    before = constants_code().replace(patcher.OFF_STEPS_OLD, "")
    w = make_workflow(before)
    with pytest.raises(SystemExit, match=re.escape(f"{patcher.CONSTANTS}: anchor found 0 times")):
        patcher.patch(w)
    assert jscode(w) == before


@pytest.mark.parametrize(("old", "new"), ANCHORS)
def test_duplicate_anchor_raises(old: str, new: str) -> None:
    w = make_workflow(constants_code() + NL + old)
    with pytest.raises(SystemExit, match=re.escape(f"{patcher.CONSTANTS}: anchor found 2 times")):
        patcher.patch(w)


def test_missing_constants_node_raises() -> None:
    w: dict[str, Any] = {"nodes": []}
    with pytest.raises(SystemExit, match=re.escape(f"{patcher.CONSTANTS} node: 0 found")):
        patcher.patch(w)
