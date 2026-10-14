"""Node test for the pure JS filter functions in dashboard.html.

The functions live in dashboard.html between the marker comments
``// === pure filter functions (no DOM) ===`` and
``// === end pure filter functions ===``.  They are extracted between the
markers, run in node via subprocess, and asserted here.  When node is not
installed the test is skipped with a reason and the manual check steps are
recorded in the docstring below.

Manual check steps (no node needed):
  1. Copy the block between the two marker comments from
     src/claude_runner/dashboard.html into a scratch file ``chip.js``.
  2. Append the assertions used below (or run the scratch file in node
     yourself): ``toggleValue``, ``removeValue``, ``clearFilter``,
     ``runMatches`` (AND across fields, OR within a field, missing fields as
     ``""``), ``filterToQuery`` (repeated params, T003 style), and
     ``parseFilter`` (garbage-tolerant).
  3. ``node chip.js`` — it must print ``all ok``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[1] / "src" / "claude_runner" / "dashboard.html"
START = "// === pure filter functions (no DOM) ==="
END = "// === end pure filter functions ==="

NODE = shutil.which("node")
if NODE is None:
    pytest.skip("node not installed; see the manual check steps in this test's docstring")

SCRIPT = """
function check(name, cond) {
  if (!cond) throw new Error(name + " failed: " + JSON.stringify(cond));
}
function eq(a, b) { return JSON.stringify(a) === JSON.stringify(b); }

// --- toggleValue: add, toggle-remove, duplicate-safe
let f = {};
toggleValue(f, "role", "worker");
check("add", eq(f, { role: ["worker"] }));
toggleValue(f, "role", "reviewer");
check("add second", eq(f, { role: ["worker", "reviewer"] }));
toggleValue(f, "role", "worker");
check("toggle-remove", eq(f, { role: ["reviewer"] }));
toggleValue(f, "role", "worker");
check("add again", eq(f, { role: ["reviewer", "worker"] }));
// a non-string / empty value is not stored
toggleValue(f, "role", "");
check("empty ignored", eq(f, { role: ["reviewer", "worker"] }));
toggleValue(f, "role", null);
check("null ignored", eq(f, { role: ["reviewer", "worker"] }));

// --- removeValue: remove one
removeValue(f, "role", "reviewer");
check("remove one", eq(f, { role: ["worker"] }));
removeValue(f, "role", "reviewer");
check("remove absent is a no-op", eq(f, { role: ["worker"] }));
// unknown fields are not created
removeValue(f, "model", "x");
check("unknown field untouched", eq(f, { role: ["worker"] }));

// --- clearFilter
f = { role: ["worker"], model: ["sonnet"] };
clearFilter(f);
check("clear", eq(f, {}));

// --- runMatches: AND across fields, OR within a field, missing fields as ""
const A = { role: "worker", model: "sonnet", status: "completed" };
const B = { role: "reviewer", model: "sonnet", status: "failed" };
const C = { role: "worker", model: "haiku" };  // status missing, so "" is used
check("AND across fields, OR within", eq(
  [
    runMatches({ role: ["worker"], status: ["completed", "failed"] }, A),
    runMatches({ role: ["worker"], status: ["completed", "failed"] }, B),
    runMatches({ role: ["worker"], status: ["completed", "failed"] }, C),
  ],
  [true, false, false],  // C fails: its status is "" and not in the list
));
// a single value per field is still OR over one value
check("single value", runMatches({ role: ["worker"] }, A) && !runMatches({ role: ["worker"] }, B));
// a field with no values does not restrict (unselected filter)
check("empty field unrestricted", runMatches({ role: [] }, A) && runMatches({ role: [] }, B));
// a missing field is matched against "", so an empty-value filter would match
check("missing field is ''", runMatches({ status: [""] }, C));
// AND: every field with values must match
check("AND fails on one field", !runMatches(
  { role: ["worker"], status: ["failed"] }, A,
));

// --- filterToQuery: repeated params as in T003
check(
  "query round-trip",
  filterToQuery({ role: ["worker", "reviewer"], status: ["completed"] })
  === "role=worker&role=reviewer&status=completed",
);
// the same filter reparsed gives the same values (repeated params round-trip)
const rt = parseFilter(filterToQuery({ role: ["worker", "reviewer"], status: ["completed"] }));
check("round-trip", eq(rt, { role: ["worker", "reviewer"], status: ["completed"] }));
// an empty filter gives an empty query
check("empty filter", filterToQuery({}) === "");
// non-string / empty values are dropped
check("dropped values", filterToQuery({ role: ["", null, "worker"] }) === "role=worker");

// --- parseFilter: tolerates garbage
check("valid string", eq(parseFilter("role=worker&role=reviewer&status=completed"),
  { role: ["worker", "reviewer"], status: ["completed"] }));
check("hash form", eq(parseFilter("#role=worker&model=sonnet"),
  { role: ["worker"], model: ["sonnet"] }));
check("query string with ?", eq(parseFilter("?role=worker"), { role: ["worker"] }));
check("unencoded &", eq(parseFilter("role=worker&role=reviewer"),
  { role: ["worker", "reviewer"] }));
check("dangling & is tolerated", eq(parseFilter("role=worker&&model=sonnet&"),
  { role: ["worker"], model: ["sonnet"] }));
check("missing = is tolerated", eq(parseFilter("role&model=sonnet"), { model: ["sonnet"] }));
check("non-string input", eq(parseFilter(null), {}));
check("undefined input", eq(parseFilter(undefined), {}));
check("number input", eq(parseFilter(42), {}));
check("object input", eq(parseFilter({}), {}));
check("garbage text", eq(parseFilter("%%%not-a-query$$$"), {}));
check("empty string", eq(parseFilter(""), {}));
check("bad escape skipped", eq(parseFilter("role=%&model=sonnet"), { model: ["sonnet"] }));
check("bad escape no throw", eq(parseFilter("#role=%E0%A4%A"), {}));

console.log("all ok");
"""


def extract_block() -> str:
    text = DASHBOARD.read_text(encoding="utf-8")
    start = text.index(START)
    end = text.index(END, start)
    return text[start:end]


def test_pure_filter_functions() -> None:
    block = extract_block()
    # the block must not touch the DOM: no document/window/localStorage/
    # getElementById/addEventListener/querySelector references
    for token in (
        "document.",
        "window.",
        "localStorage",
        "getElementById",
        "addEventListener",
        "querySelector",
    ):
        assert token not in block, f"DOM reference found in block: {token}"

    result = subprocess.run(
        [NODE, "--input-type=module", "-e", block + "\n" + SCRIPT],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "node test failed:\n" + result.stdout + "\n" + result.stderr
    assert "all ok" in result.stdout


def test_esc_function() -> None:
    """The esc() function escapes &, <, >, ", ' to HTML entities.
    Apply it to a string with quotes and angle brackets and assert the escaped output."""
    text = DASHBOARD.read_text(encoding="utf-8")
    # Extract the esc function definition (a single-line arrow function)
    esc_start = text.index("const esc = ")
    esc_end = text.index("\n", esc_start)
    esc_line = text[esc_start:esc_end]
    assert esc_line.startswith("const esc = ")

    # Build a JS script that defines check(), includes the esc function,
    # and asserts the escaped output for '"<x>' and individual characters.
    # In the JS, the string '"<x>' is written as '"<x>' using a JS single-quoted
    # string with an escaped single quote, or as a double-quoted string.
    # The expected escaped output of '"<x>' is: &quot;&#39;&lt;x&gt;
    # The JS source we build. The esc argument for the main test is the 5-character
    # string: double-quote, single-quote, less-than, x, greater-than.
    # In JS we write it as a single-quoted string with the inner single quote
    # escaped: '"\'<x>'
    # Build the JS script. The esc argument for the main test is the 5-character
    # string: double-quote, single-quote, less-than, x, greater-than.
    # In JS we write it as a single-quoted string with the inner single quote
    # escaped: '"\'<x>'
    js_lines = [
        "function check(name, cond) {",
        "  if (!cond) throw new Error(name + ' failed: ' + JSON.stringify(cond));",
        "}",
        esc_line,
        'check("esc quotes and angle brackets", esc(\'"\\\'<x>\') === "&quot;&#39;&lt;x&gt;");',
        'check("esc amp", esc("&") === "&amp;");',
        'check("esc lt", esc("<") === "&lt;");',
        'check("esc gt", esc(">") === "&gt;");',
        'check("esc double quote", esc(\'"\') === "&quot;");',
        'check("esc single quote", esc("\'") === "&#39;");',
        'check("esc mixed", esc("a<&>\'") === "a&lt;&amp;&gt;&#39;");',
        "console.log('all ok');",
    ]
    js_script = "\n".join(js_lines)

    result = subprocess.run(
        [NODE, "--input-type=module", "-e", js_script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "node esc test failed:\n" + result.stdout + "\n" + result.stderr
    assert "all ok" in result.stdout
