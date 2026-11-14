"""Patch tests for features_config_patch.py: the Constants defaults come from one file.

The patch reads ahawr-runner/src/ahawr_runner/features_defaults.json (T013), so these tests
prove the file is read and not copied: no default literal in the patch source, a value
changed only in a temporary defaults file reaching the Constants text, idempotency, every
anchor asserted exactly once and a node syntax check of the text the patch writes. The
pinned test regenerates the Constants text from a copy of the live export and compares it
byte for byte with tests/fixtures/features_constants_pinned.txt, the text the workflow node
carries (T018 restored that fixture and its test after the hand-kept-copy parity test was
dropped); each other consumer has its own test against the single file (features_config.py,
the JS panel in board_features.test.js).
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PATCH_PATH = REPO_ROOT / "data-tables" / "ops" / "features_config_patch.py"
EXPORT_PATH = REPO_ROOT / "data-tables" / "ops" / "_scratch" / "live_export.json"
DEFAULTS_SOURCE = REPO_ROOT / "ahawr-runner" / "src" / "ahawr_runner" / "features_defaults.json"
DEFAULTS_TEXT = DEFAULTS_SOURCE.read_text(encoding="utf-8")
FIXTURES_DIR = REPO_ROOT / "ahawr-runner" / "tests" / "fixtures"
PINNED_CONSTANTS_PATH = FIXTURES_DIR / "features_constants_pinned.txt"

# A "<param>: <value>" pair in the patch source is a default living in the patch instead of
# in features_defaults.json; the parameter names come from that file.
def _default_parameter_names() -> list[str]:
    """Every parameter name of the single defaults file, sorted for a stable regex."""
    data = json.loads(DEFAULTS_TEXT)
    names = set(data["param_minimums"])
    for body in data["features"].values():
        names.update(body)
    return sorted(names)


PATCH_DEFAULT_LITERAL = re.compile(
    r"(?:" + "|".join(_default_parameter_names()) + r")\s*:\s*(?:true|false|-?\d+)"
)

# A number never shipped in features_defaults.json, used only to change one default in a
# temporary copy of that file.
TEMP_SPLIT_STEPS = 4242


def load_patch() -> ModuleType:
    spec = importlib.util.spec_from_file_location("features_config_patch", PATCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


patcher = load_patch()

# shortcut: the minimal node carries only the two patch anchors, upgrade: pin the full live
# Constants text here if the export copy ever stops being available to the test run
MINIMAL_CONSTANTS_CODE = "\n".join([
    "const row = rows[0];",
    "const mission = {};",
    patcher.RETURN_OLD,
    patcher.MAX_TASKS_OLD,
    "} }];",
])


def run_patch(path: Path, script: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Runs the patch on ``path``; ``script`` defaults to the real patch file."""
    return subprocess.run(
        [sys.executable, str(script or PATCH_PATH), str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def constants_code(path: Path) -> str:
    raw = json.loads(path.read_text(encoding="utf-8"))
    workflow = raw[0] if isinstance(raw, list) else raw
    return str(patcher.node_named(workflow, patcher.CONSTANTS)["parameters"]["jsCode"])


def stage_patch_copy(tmp_path: Path, patch_source: Path) -> Path:
    """A copy of the patch two levels deep: it resolves the defaults file under tmp_path.
    shortcut: the patch finds the defaults file relative to itself, upgrade: pass the file
    to the patch as an argument."""
    copy = tmp_path / "ops" / "deep" / "patch_copy.py"
    copy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(patch_source, copy)
    return copy


def stage_defaults(tmp_path: Path, defaults_text: str | None) -> Path:
    """The stand-in features_defaults.json the staged patch copy reads, under tmp_path only."""
    defaults = tmp_path / "ahawr-runner" / "src" / "ahawr_runner" / "features_defaults.json"
    defaults.parent.mkdir(parents=True, exist_ok=True)
    if defaults_text is not None:
        defaults.write_text(defaults_text, encoding="utf-8")
    return defaults


def stage_flow(tmp_path: Path, js_code: str) -> Path:
    """A workflow holding only the Constants node, written under tmp_path."""
    flow = tmp_path / "flow.json"
    node = {"name": patcher.CONSTANTS, "parameters": {"jsCode": js_code}}
    flow.write_text(json.dumps({"nodes": [node]}), encoding="utf-8")
    return flow


def test_features_defaults_patch_source_has_no_literal_defaults() -> None:
    """The patch carries no default value of its own: changing a default needs no patch edit."""
    source = PATCH_PATH.read_text(encoding="utf-8")
    literals = PATCH_DEFAULT_LITERAL.findall(source)
    assert not literals, f"features_config_patch.py still carries default literals: {literals}"
    assert "features_defaults.json" in source, "the patch does not read the single defaults file"


def test_features_defaults_a_value_changed_in_a_temp_file_reaches_the_constants_text(
    tmp_path: Path,
) -> None:
    """The Constants text of a patched copy carries the value of the temporary defaults file,
    not the value of the shipped one: the patch reads the file, it holds no copy of it."""
    shipped = json.loads(DEFAULTS_TEXT)
    changed = json.loads(DEFAULTS_TEXT)
    changed["features"]["adaptive_split"]["split_steps"] = TEMP_SPLIT_STEPS
    shipped_steps = shipped["features"]["adaptive_split"]["split_steps"]
    assert shipped_steps != TEMP_SPLIT_STEPS, "the temporary value is already shipped"

    patch_copy = stage_patch_copy(tmp_path, PATCH_PATH)
    defaults = stage_defaults(tmp_path, json.dumps(changed))
    flow = stage_flow(tmp_path, MINIMAL_CONSTANTS_CODE)
    proc = run_patch(flow, patch_copy)
    assert proc.returncode == 0, proc.stderr
    assert defaults.is_file(), "the temporary defaults file was not written"

    code = constants_code(flow)
    assert f"split_steps: {TEMP_SPLIT_STEPS}" in code, "the Constants text ignores the file"
    assert f"split_steps: {shipped_steps}" not in code, "the Constants text keeps the old value"


def test_features_defaults_patching_a_copy_twice_leaves_the_copy_identical(tmp_path: Path) -> None:
    """The second run of the patch writes nothing: the core is already there and current."""
    patch_copy = stage_patch_copy(tmp_path, PATCH_PATH)
    stage_defaults(tmp_path, DEFAULTS_TEXT)
    flow = stage_flow(tmp_path, MINIMAL_CONSTANTS_CODE)

    first = run_patch(flow, patch_copy)
    assert first.returncode == 0, first.stderr
    assert "patched Constants" in first.stdout, first.stdout
    first_bytes = flow.read_bytes()
    code = constants_code(flow)
    assert code.count(patcher.CORE_START) == 1, "the core block is inserted more than once"
    assert code.count(patcher.CORE_END) == 1, "the core end marker is inserted more than once"
    assert code.count(patcher.MAX_TASKS_NEW) == 1, "the flattened constants are written twice"

    second = run_patch(flow, patch_copy)
    assert second.returncode == 0, second.stderr
    assert "already patched" in second.stdout, second.stdout
    assert flow.read_bytes() == first_bytes, "the second run changed the copy"


@pytest.mark.skipif(not EXPORT_PATH.exists(), reason="the live export copy is not available")
def test_features_defaults_live_export_copy_is_patched_from_the_json_and_left_untouched(
    tmp_path: Path,
) -> None:
    """The patch runs only on a copy under tmp_path: every default of the JSON shows up in the
    Constants text of that copy, the second run writes nothing, the live export bytes stay."""
    live_before = EXPORT_PATH.read_bytes()
    copy = tmp_path / "live_export_copy.json"
    shutil.copyfile(EXPORT_PATH, copy)

    first = run_patch(copy)
    assert first.returncode == 0, first.stderr
    assert "patched Constants" in first.stdout, first.stdout
    code = constants_code(copy)
    for feature, body in json.loads(DEFAULTS_TEXT)["features"].items():
        if feature in patcher.CORE_FEATURES:
            for param, value in body.items():
                needle = f"{param}: {patcher.js_value(value)}"
                assert needle in code, f"{feature}.{param}={value} is not in the Constants text"

    first_bytes = copy.read_bytes()
    second = run_patch(copy)
    assert second.returncode == 0, second.stderr
    assert "already patched" in second.stdout, second.stdout
    assert copy.read_bytes() == first_bytes, "the second run changed the copy"
    assert EXPORT_PATH.read_bytes() == live_before, "the live export copy was modified"


@pytest.mark.skipif(not EXPORT_PATH.exists(), reason="the live export copy is not available")
@pytest.mark.skipif(not PINNED_CONSTANTS_PATH.is_file(), reason="the pinned fixture is missing")
def test_features_defaults_pinned_constants_text_matches_the_patch_output(tmp_path: Path) -> None:
    """The refactored patch writes the pinned Constants text on a copy of the live export: the
    single defaults file still produces the text the workflow node carries, byte for byte."""
    live_before = EXPORT_PATH.read_bytes()
    copy = tmp_path / "live_export_copy.json"
    shutil.copyfile(EXPORT_PATH, copy)
    proc = run_patch(copy)
    assert proc.returncode == 0, proc.stderr
    assert "patched Constants" in proc.stdout, proc.stdout

    pinned = PINNED_CONSTANTS_PATH.read_bytes()
    generated = constants_code(copy).encode("utf-8")
    assert generated == pinned, (
        f"the patched Constants text differs from the pin: {len(generated)} bytes vs {len(pinned)}"
    )
    assert EXPORT_PATH.read_bytes() == live_before, "the live export copy was modified"


def test_features_defaults_every_anchor_is_asserted_exactly_once(tmp_path: Path) -> None:
    """patch() asserts each anchor with once() exactly once, and a copy without or with a
    doubled anchor aborts with the count of the anchor, writing nothing."""
    source = PATCH_PATH.read_text(encoding="utf-8")
    for anchor in ("RETURN_OLD", "MAX_TASKS_OLD"):
        calls = source.count(f"once(code, {anchor},")
        assert calls == 1, f"the anchor {anchor} is asserted {calls} times in patch()"

    patch_copy = stage_patch_copy(tmp_path, PATCH_PATH)
    stage_defaults(tmp_path, DEFAULTS_TEXT)

    missing = MINIMAL_CONSTANTS_CODE.replace(patcher.RETURN_OLD, "return [")
    flow = stage_flow(tmp_path, missing)
    before = flow.read_text(encoding="utf-8")
    proc = run_patch(flow, patch_copy)
    assert proc.returncode != 0, proc.stdout
    assert "anchor found 0 times, expected 1" in proc.stderr, proc.stderr
    assert flow.read_text(encoding="utf-8") == before, "the abort wrote the flow copy"

    doubled = MINIMAL_CONSTANTS_CODE + "\n" + patcher.RETURN_OLD
    flow = stage_flow(tmp_path, doubled)
    before = flow.read_text(encoding="utf-8")
    proc = run_patch(flow, patch_copy)
    assert proc.returncode != 0, proc.stdout
    assert "anchor found 2 times, expected 1" in proc.stderr, proc.stderr
    assert flow.read_text(encoding="utf-8") == before, "the abort wrote the flow copy"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not available")
def test_features_defaults_patched_constants_text_passes_node_syntax_check(tmp_path: Path) -> None:
    """The text the patch writes into the Constants node compiles as JavaScript."""
    patch_copy = stage_patch_copy(tmp_path, PATCH_PATH)
    stage_defaults(tmp_path, DEFAULTS_TEXT)
    flow = stage_flow(tmp_path, MINIMAL_CONSTANTS_CODE)
    proc = run_patch(flow, patch_copy)
    assert proc.returncode == 0, proc.stderr

    # shortcut: the Constants jsCode is a function body with a bare return, so node needs the
    # wrapper; upgrade: run the syntax check on the real node when the export is patched.
    checked = tmp_path / "constants_syntax_check.js"
    checked.write_text(f"function __constants() {{\n{constants_code(flow)}\n}}\n", encoding="utf-8")
    check = subprocess.run(
        ["node", "--check", str(checked)],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert check.returncode == 0, check.stderr


def test_features_defaults_missing_or_malformed_json_aborts_the_patch(tmp_path: Path) -> None:
    """No silent fallback: the patch names the file and the reason it refused to write."""
    patch_copy = stage_patch_copy(tmp_path, PATCH_PATH)
    flow = stage_flow(tmp_path, MINIMAL_CONSTANTS_CODE)

    proc = run_patch(flow, patch_copy)
    assert proc.returncode != 0, proc.stderr
    assert "not found" in proc.stderr

    bad_texts = (
        ("{oops", "not valid JSON"),
        ('{"features": {"plan_no_truncate": {"enabled": false}}}', "param_minimums"),
    )
    for text, reason in bad_texts:
        stage_defaults(tmp_path, text)
        proc = run_patch(flow, patch_copy)
        assert proc.returncode != 0, proc.stderr
        assert reason in proc.stderr, proc.stderr
