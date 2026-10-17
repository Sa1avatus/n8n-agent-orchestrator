"""Pytest wrapper around the node test for board_queue.js (the top-level
board render).  The pure render functions live in
src/claude_runner/board_queue.js and are tested by the node script
tests/board_queue.test.js.  When node is not installed the tests are
skipped; the manual check steps are below.

Manual check steps (no node needed):
  1. Open src/claude_runner/board_queue.js in a browser console or scratch
     file and load it.
  2. Run the assertions from tests/board_queue.test.js by hand:
     ``renderQueue`` (the five groups in order, the mission cards with
     name / mission_id / state / task n of total / progress bar / total
     attempts / time / tokens / cache share / cost / avg tok/s / thinking
     share, the ``#board/<mission_id>`` links), ``esc`` (script tags and
     quotes in names, ids and group keys never reach the DOM unescaped),
     the empty board (the five headers stay, no cards).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
QUEUE = HERE.parent / "src" / "claude_runner" / "board_queue.js"
NODE_TEST = HERE / "board_queue.test.js"

NODE = shutil.which("node")
if NODE is None:
    pytest.skip("node not installed; see the manual check steps in this "
                "test's docstring")


def test_board_queue_render() -> None:
    """Run the node test; it asserts the five groups, every card field,
    the deep links, and that every value goes through esc()."""
    result = subprocess.run(
        [NODE, str(NODE_TEST)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "node test failed:\n" + result.stdout + "\n" + result.stderr
    )
    assert "all ok" in result.stdout


def test_board_queue_no_unescaped_values() -> None:
    """The module must escape every value: no unescaped interpolation of
    server data (the only innerHTML is the esc()-built card HTML)."""
    text = QUEUE.read_text(encoding="utf-8")
    # no unescaped concatenation of a data variable into HTML
    for token in ("+ mission", "+ live", "+ stats", "+ views"):
        assert token not in text, f"unescaped value interpolation found: {token}"


def test_board_queue_module_loadable() -> None:
    """Require the module in node and check the exported API surface."""
    script = (
        "const BQ = require(process.argv[1]);\n"
        "for (const name of ['renderQueue','cardHtml','BoardQueue','render','esc']) {\n"
        "  if (typeof BQ[name] !== 'function') throw new Error("
        "name + ' is not exported');\n"
        "}\n"
        "if (!Array.isArray(BQ.GROUPS)) throw new Error('GROUPS not exported');\n"
        "console.log('all ok');\n"
    )
    result = subprocess.run(
        [NODE, "-e", script, str(QUEUE)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "module load failed:\n" + result.stdout + "\n" + result.stderr
    )
    assert "all ok" in result.stdout
