from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
PATCH_PATH = ROOT / "data-tables" / "ops" / "review_calibration_patch.py"
EXPORT_PATH = ROOT / "AHAWR_v13_ClaudeCode.json"

pytestmark = pytest.mark.skipif(
    not (PATCH_PATH.is_file() and EXPORT_PATH.is_file()),
    reason=f"needs {PATCH_PATH} and {EXPORT_PATH}; data-tables/ops is not in git",
)

EXPECTED_MARKERS = {
    "Parse Plan": "task_type",
    "Restore Saved Task State": "task_type",
    "Build Reviewer Run Input": "REVIEW_TYPE_TEXT",
    "Constants": "review_escalation_enabled",
    "Retry Current Task": "escalation_to_architect",
    "Build Architect Run Input": "ESCALATION TO ARCHITECT",
}
RETRY_ANCHOR = "if (currentAttempt >= limit) {"


def _load_patch() -> ModuleType:
    spec = importlib.util.spec_from_file_location("review_calibration_patch", PATCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_patch(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["review_calibration_patch.py", str(path)])
    _load_patch().main()


def _workflow(raw: Any) -> dict[str, Any]:
    workflow: dict[str, Any] = raw[0] if isinstance(raw, list) else raw
    return workflow


def _load_workflow(path: Path) -> dict[str, Any]:
    workflow: dict[str, Any] = _workflow(json.loads(path.read_text(encoding="utf-8")))
    return workflow


def _nodes(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["name"]: node for node in workflow["nodes"]}


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def export_copy(tmp_path: Path) -> Path:
    copy = tmp_path / EXPORT_PATH.name
    copy.write_bytes(EXPORT_PATH.read_bytes())
    return copy


@pytest.fixture
def patched_copy(export_copy: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _run_patch(export_copy, monkeypatch)
    return export_copy


def test_patch_changes_exactly_the_expected_nodes(patched_copy: Path) -> None:
    before = _nodes(_load_workflow(EXPORT_PATH))
    after = _nodes(_load_workflow(patched_copy))

    assert list(after) == list(before)
    changed = {name for name in before if _dump(before[name]) != _dump(after[name])}
    assert changed == set(EXPECTED_MARKERS)
    for name, marker in EXPECTED_MARKERS.items():
        assert marker not in before[name]["parameters"]["jsCode"], name
        assert marker in after[name]["parameters"]["jsCode"], name


def test_other_nodes_and_top_level_keys_are_unchanged(patched_copy: Path) -> None:
    original = _load_workflow(EXPORT_PATH)
    patched = _load_workflow(patched_copy)

    assert set(patched) == set(original)
    for key in set(original) - {"nodes"}:
        assert _dump(patched[key]) == _dump(original[key]), key

    original_nodes = _nodes(original)
    patched_nodes = _nodes(patched)
    for name in set(original_nodes) - set(EXPECTED_MARKERS):
        assert _dump(patched_nodes[name]) == _dump(original_nodes[name]), name


def test_second_run_leaves_the_file_byte_identical(
    patched_copy: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    first_run = patched_copy.read_bytes()

    _run_patch(patched_copy, monkeypatch)

    assert "already patched" in capsys.readouterr().out
    assert patched_copy.read_bytes() == first_run


def test_missing_anchor_fails_and_writes_nothing(
    export_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = json.loads(export_copy.read_text(encoding="utf-8"))
    node = _nodes(_workflow(raw))["Retry Current Task"]
    assert RETRY_ANCHOR in node["parameters"]["jsCode"]
    node["parameters"]["jsCode"] = node["parameters"]["jsCode"].replace(RETRY_ANCHOR, "")
    export_copy.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    before = export_copy.read_bytes()

    with pytest.raises(SystemExit, match="anchor for Retry Current Task found 0 times"):
        _run_patch(export_copy, monkeypatch)

    assert export_copy.read_bytes() == before
    assert not export_copy.with_name(export_copy.name + ".tmp").exists()


def test_original_export_is_not_touched(
    export_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _sha256(EXPORT_PATH)

    _run_patch(export_copy, monkeypatch)

    assert _sha256(EXPORT_PATH) == before
