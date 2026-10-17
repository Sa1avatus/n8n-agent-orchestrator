"""Pytest wrapper around the node test for board_logic.js.

The pure functions live in src/claude_runner/board_logic.js and are tested
by the node script tests/board_logic.test.js.  When node is not installed
the tests are skipped; the manual check steps are below.

Manual check steps (no node needed):
  1. Open src/claude_runner/board_logic.js in a browser console or scratch
     file and load it.
  2. Run the assertions from tests/board_logic.test.js by hand:
     ``reorder`` (front/middle/end, clamping, purity, missing id),
     ``dropPosition`` (index valid for ``reorder``, chained
     ``reorder`` calls giving the expected order, drop onto self as a
     no-op, running-mission refusal, running target must stay first,
     group-header drop at the front),
     ``mergeServerIntoForm`` (local edits kept, server fields taken over,
     ``server_changed`` flag set/cleared), ``parseHash``/``buildHash``
     (#board, #board/<mission_id>, #board/<mission_id>/T003 round-trip),
     ``esc`` (quotes and angle brackets),
     ``diffConfig`` (changed fields with old/new, sorted, added/removed
     keys), ``thinkLayout`` (weights laid out from from_task, broken
     policy yields nulls).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
LOGIC = HERE.parent / "src" / "claude_runner" / "board_logic.js"
NODE_TEST = HERE / "board_logic.test.js"

NODE = shutil.which("node")
if NODE is None:
    pytest.skip("node not installed; see the manual check steps in this "
                "test's docstring")


def test_board_logic_pure_functions() -> None:
    """Run the node test; it asserts reorder, dropPosition, merge, hash,
    esc, diffConfig and thinkLayout."""
    result = subprocess.run(
        [NODE, str(NODE_TEST)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "node test failed:\n" + result.stdout + "\n" + result.stderr
    )
    assert "all ok" in result.stdout


def test_board_logic_no_dom() -> None:
    """The module must not touch the DOM: no document/window/localStorage."""
    text = LOGIC.read_text(encoding="utf-8")
    for token in (
        "document.",
        "window.",
        "localStorage",
        "getElementById",
        "addEventListener",
        "querySelector",
    ):
        assert token not in text, f"DOM reference found in board_logic.js: {token}"


def test_board_logic_module_loadable() -> None:
    """Require the module in node and check the exported API surface."""
    script = (
        "const BL = require(process.argv[1]);\n"
        "for (const name of ['esc','reorder','dropPosition',"
        "'mergeServerIntoForm','parseHash','buildHash',"
        "'diffConfig','thinkLayout']) {\n"
        "  if (typeof BL[name] !== 'function') throw new Error("
        "name + ' is not exported');\n"
        "}\n"
        "console.log('all ok');\n"
    )
    result = subprocess.run(
        [NODE, "-e", script, str(LOGIC)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "module load failed:\n" + result.stdout + "\n" + result.stderr
    )
    assert "all ok" in result.stdout
