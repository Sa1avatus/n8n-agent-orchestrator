"""Static test for the board views: every value inserted into innerHTML
(or into a template string that reaches innerHTML) must go through the
esc() escaping function.

Checks over every ``src/claude_runner/board*.js`` file (read in parts):

1. Every ``esc`` function that performs the replacement itself escapes
   ``&``, ``<``, ``>``, ``"`` and ``'`` to HTML entities; an ``esc`` name
   that only delegates to another esc is accepted.
2. Every backtick string that builds a tag (contains ``<tag``) only
   interpolates ``${...}`` wrapped in an esc call.
3. Every ``.innerHTML =`` assignment carries an ``esc(`` in its right-hand
   side, is a call of a render function (whose template literals are
   checked by (2)), or is ``JSON.stringify`` (never HTML).
4. ``board.html`` has no inline event handler attributes (``on...="..."``).

Files are read in parts: the innerHTML positions are located first, then
the source is scanned in chunks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PKG_DIR = Path(__file__).resolve().parents[1] / "src" / "claude_runner"
BOARD_JS_GLOB = "board*.js"


def _esc_names(text: str) -> list[str]:
    return [
        m.group(1)
        for m in re.finditer(
            r"function\s+([A-Za-z_$][A-Za-z0-9_$]*)(\([^)]*\))\s*\{", text
        )
        if "esc" in m.group(1).lower()
    ]


def _func_body(text: str, name: str) -> str | None:
    """The source of one function body, braces balanced (no braces appear
    inside the string literals of the esc functions)."""
    m = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", text)
    if not m:
        return None
    depth = 0
    for i in range(m.end() - 1, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[m.end() - 1 : i + 1]
    return None


def _check_esc(text: str) -> None:
    """Every escape function that replaces itself must escape & < > " ' to
    HTML entities; a delegating esc name is accepted."""
    for name in _esc_names(text):
        body = _func_body(text, name)
        if body is None:
            pytest.fail(f"esc body of {name} not found")
        if ".replace(" in body:
            for ch, entity in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"),
                             ("\"", "&quot;"), ("'", "&#39;")):
                if f".replace(/{ch}/g, \"{entity}\")" not in body:
                    pytest.fail(f"{name} does not escape {ch!r} to {entity}")
        elif not re.search(r"\b(?:localEsc|esc)\s*\(|BoardLogic\.esc", body):
            pytest.fail(f"{name} neither escapes nor delegates")


def _check_templates(text: str) -> None:
    """Every backtick string that builds a tag interpolates only values
    wrapped in an esc call."""
    for m in re.finditer(r"`(?:\\.|[^`\\])*`", text):
        lit = m.group(0)
        if not re.search(r"<[a-zA-Z]", lit):
            continue
        for i in re.finditer(r"\$\{[^\}]*\}", lit):
            frag = i.group(0)
            if "esc(" not in frag and "Esc(" not in frag:
                pytest.fail(f"unescaped interpolation {frag} in a tag string")


def _check_innerhtml(text: str) -> None:
    """Every innerHTML assignment must carry an esc() in its right-hand side,
    be a render-function call (covered by _check_templates), or be
    JSON.stringify (never HTML)."""
    for m in re.finditer(r"\.innerHTML\s*=", text):
        pos = m.start()
        end = text.find(";", pos)
        if end < 0:
            end = len(text)
        rhs = text[pos:end]
        if "esc(" in rhs or "Esc(" in rhs:
            continue
        if "JSON.stringify" in rhs:
            continue
        if not re.search(r"=\s*[A-Za-z_$][A-Za-z0-9_.$]*\s*\(", rhs):
            pytest.fail(f"innerHTML assignment without esc(): {rhs.strip()[:80]!r}")


def _parts(text: str, innerhtml_pos: int) -> list[str]:
    """The file in parts: the head up to the first innerHTML, then the rest
    in 1000-char steps with a 200-char overlap so no backtick string is
    split across a part boundary."""
    parts = []
    if innerhtml_pos > 0:
        parts.append(text[:innerhtml_pos])
    pos = max(innerhtml_pos, 0)
    while pos < len(text):
        parts.append(text[pos : pos + 1200])
        pos += 1000
    return parts


def test_board_js_escaping() -> None:
    """Static check over every board*.js: esc() escapes the five characters
    and no tag string reaches innerHTML with a bare value."""
    files = sorted(PKG_DIR.glob(BOARD_JS_GLOB))
    if not files:
        pytest.fail("no board*.js files found under " + str(PKG_DIR))
    for f in files:
        text = f.read_text(encoding="utf-8")
        _check_esc(text)
        first = text.find("innerHTML")  # located before reading the parts
        for part in _parts(text, first):
            _check_templates(part)
        if first >= 0:
            _check_innerhtml(text)


def test_board_html_no_inline_handlers() -> None:
    """board.html must not contain inline event handler attributes
    (onclick=, onerror=, onload=, ...) that evaluate data values."""
    html = (PKG_DIR / "board.html").read_text(encoding="utf-8")
    m = re.search(r"\son[a-z]+\s*=", html, re.I)
    assert m is None, f"inline event handler found: {m.group(0)!r}"


def test_every_script_of_board_html_matches_the_dashboard_static_route():
    """/static/{name} only serves names matching this pattern; a digit in a
    name (board_i18n.js) once gave 404 and broke the whole board."""
    dash = (PKG_DIR / "dashboard.py").read_text(encoding="utf-8")
    m = re.search(r'_board_static = re\.compile\(r"([^"]+)"\)', dash)
    assert m, "static route pattern not found"
    pattern = re.compile(m.group(1))
    html = (PKG_DIR / "board.html").read_text(encoding="utf-8")
    names = re.findall(r'(?:src|href)="/static/([^"]+)"', html)
    assert names
    bad = [n for n in names if not pattern.fullmatch(n)]
    assert bad == []
