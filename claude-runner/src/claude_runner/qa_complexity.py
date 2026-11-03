"""Function length and nesting depth, checked against a saved baseline.

The QA complexity gate measures every ``def`` of a Python file with plain ``ast``:
``function_length_lines`` gives each function's span in lines,
``function_nesting_depth`` gives its deepest block nesting, and ``find_violations``
compares the measured values with the baseline JSON (file -> function ->
{length_lines, nesting_depth}). A touched file violates when a function grows past
its saved number; a function the baseline does not name is checked against the
thresholds from settings. Equal is not worse. ``check_c901`` runs the installed ruff
with --select C901 and the mccabe threshold from settings; the violations have the
same shape as ``find_violations``, with the metric ``complexity``.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

# The metrics the gate tracks: the same keys in the measured and baseline shapes.
METRICS: tuple[str, ...] = ("length_lines", "nesting_depth")
# The C901 metric ruff measures: the mccabe complexity score of one function.
COMPLEXITY_METRIC = "complexity"
# Bound on one ruff call in ``check_c901``; a ruff that hangs gives NOT RUN.
RUFF_TIMEOUT_SECONDS = 30


class ComplexityError(ValueError):
    """The file could not be measured because its syntax is broken."""


def function_length_lines(path: str | Path) -> dict[str, int]:
    """Return each function's span in lines, keyed by the function name."""
    lengths: dict[str, int] = {}
    for function in _parse_functions(path):
        # shortcut: two defs with the same name in one file share one key (the last
        # one wins); qualify with the class name when a file needs both.
        end_line = function.end_lineno or function.lineno
        lengths[function.name] = end_line - function.lineno + 1
    return lengths


def function_nesting_depth(path: str | Path) -> dict[str, int]:
    """Return the deepest block nesting of each function, keyed by the function name.

    Nesting counts block statements (if, for, while, try, with, def, class, match),
    with the function body itself at level 0.
    """
    depths: dict[str, int] = {}
    for function in _parse_functions(path):
        # shortcut: lambdas and comprehensions are not counted as nesting levels;
        # extend _nested_bodies when the gate needs them.
        depths[function.name] = _max_depth(function.body, 0)
    return depths


def find_violations(
    measured: Mapping[str, Mapping[str, Mapping[str, int]]],
    baseline: Mapping[str, Mapping[str, Mapping[str, int]]],
    thresholds: Mapping[str, int],
    touched_files: Iterable[str],
) -> list[dict[str, Any]]:
    """Violations of the touched files: {file, function, metric, value, baseline}.

    A metric is worse when its value is greater than its limit; equal passes. The
    limit is the saved baseline entry for that function when it has one, otherwise
    the threshold from settings; a metric with neither limit is not checked. Files
    not in ``touched_files`` are skipped, so an untouched file never reports a
    violation even when its numbers grow.
    """
    touched = set(touched_files)
    violations: list[dict[str, Any]] = []
    for file, functions in measured.items():
        if file not in touched:
            continue
        saved_functions = baseline.get(file, {})
        for function_name, values in functions.items():
            saved_limits = saved_functions.get(function_name, {})
            for metric in METRICS:
                limit = saved_limits.get(metric)
                if limit is None:
                    limit = thresholds.get(metric)
                value = values[metric]
                if limit is not None and value > limit:
                    violations.append(
                        {
                            "file": file,
                            "function": function_name,
                            "metric": metric,
                            "value": value,
                            "baseline": limit,
                        }
                    )
    return violations


def check_c901(
    files: Iterable[str],
    max_complexity: int,
    baseline: Mapping[str, Mapping[str, Mapping[str, int]]] = {},
    ruff_bin: str = "ruff",
) -> dict[str, Any]:
    """Run ruff's C901 rule on ``files`` and compare with the baseline.

    The threshold comes from settings via ``max_complexity``, and a function's limit
    is its saved baseline entry when it has one, otherwise the threshold; equal is
    not worse. Returns {"verdict": "PASS" | "FAIL" | "NOT RUN", "violations": the
    same shape as find_violations, "reason": None or why the run was not made}.
    A missing ruff, a ruff that times out or exits with a configuration error gives
    "NOT RUN" with the reason instead of raising.
    """
    file_list = list(files)
    if not file_list:
        return {"verdict": "NOT RUN", "violations": [], "reason": "no files were given"}
    config_path = _c901_ruff_config(max_complexity)
    try:
        completed = subprocess.run(
            [
                ruff_bin,
                "check",
                "--select",
                "C901",
                "--output-format",
                "json",
                "--config",
                config_path,
                *file_list,
            ],
            capture_output=True,
            text=True,
            timeout=RUFF_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return {
            "verdict": "NOT RUN",
            "violations": [],
            "reason": f"ruff is not installed: {ruff_bin} not found",
        }
    except subprocess.TimeoutExpired:
        return {
            "verdict": "NOT RUN",
            "violations": [],
            "reason": f"ruff did not finish within {RUFF_TIMEOUT_SECONDS} seconds",
        }
    finally:
        Path(config_path).unlink()
    # ruff exits 1 when it finds violations; anything else is a ruff/usage error.
    if completed.returncode not in (0, 1):
        return {
            "verdict": "NOT RUN",
            "violations": [],
            "reason": f"ruff failed: {completed.stderr.strip()}",
        }
    try:
        entries = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as error:
        raise ComplexityError(f"ruff C901 output is not JSON: {error}") from error
    violations: list[dict[str, Any]] = []
    for entry in entries:
        violation = _c901_violation(entry, baseline, max_complexity)
        if violation is not None:
            violations.append(violation)
    return {
        "verdict": "FAIL" if violations else "PASS",
        "violations": violations,
        "reason": None,
    }


def _c901_ruff_config(max_complexity: int) -> str:
    """Write the mccabe threshold to a throwaway ruff config and return its path.

    shortcut: installed ruff 0.17 has no --max-complexity command-line flag (the
    threshold lives only in a config file); pass the flag directly when a ruff
    supports it again.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as handle:
        handle.write(f"[lint.mccabe]\nmax-complexity = {max_complexity}\n")
    return handle.name


def _c901_violation(
    entry: Mapping[str, Any],
    baseline: Mapping[str, Mapping[str, Mapping[str, int]]],
    max_complexity: int,
) -> dict[str, Any] | None:
    """The violation shape from one ruff JSON entry; None when it is not a C901 hit.

    The function name comes from the ruff message ("`outer` is too complex (5 > 1)")
    or from the def at the reported line when the message names no function.
    """
    if entry.get("code") != "C901":
        return None
    message = str(entry.get("message", ""))
    match = re.search(r"`([A-Za-z_][A-Za-z0-9_]*)` is too complex \((\d+)", message)
    function_name: str | None = None
    if match is not None:
        function_name, value = match.group(1), int(match.group(2))
    else:
        # shortcut: older ruff wrote "Complex (N) > allowed complexity (N)"; name the
        # function from the reported line instead until an older ruff is pinned.
        old = re.search(r"Complex \((\d+)\)", message)
        if old is None:
            return None
        value = int(old.group(1))
    file = str(entry.get("filename", ""))
    if function_name is None:
        row = entry.get("location", {}).get("row")
        if isinstance(row, int):
            function_name = _function_at_line(file, row)
    saved = baseline.get(file, {}).get(function_name or "", {}).get(COMPLEXITY_METRIC)
    limit = saved if saved is not None else max_complexity
    if value > limit:
        return {
            "file": file,
            "function": function_name,
            "metric": COMPLEXITY_METRIC,
            "value": value,
            "baseline": limit,
        }
    return None


def _function_at_line(path: str | Path, line: int) -> str | None:
    """The name of the def whose span contains ``line``; None when no def does."""
    for function in _parse_functions(path):
        end_line = function.end_lineno or function.lineno
        if function.lineno <= line <= end_line:
            return function.name
    return None


def _parse_functions(path: str | Path) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Parse the file and list every def in it; a clear error when the syntax is broken."""
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except SyntaxError as error:
        raise ComplexityError(
            f"{path}: syntax error on line {error.lineno}: {error.msg}"
        ) from error
    functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            functions.append(node)
    return functions


def _max_depth(body: list[ast.stmt], level: int) -> int:
    """The deepest nesting level among these statements, starting at ``level``."""
    deepest = level
    for statement in body:
        for child_body in _nested_bodies(statement):
            deepest = max(deepest, _max_depth(child_body, level + 1))
    return deepest


def _nested_bodies(statement: ast.stmt) -> list[list[ast.stmt]]:
    """The blocks a compound statement opens; an empty block counts as no nesting."""
    blocks: list[list[ast.stmt]] = []
    for field in ("body", "orelse", "finalbody"):
        block = getattr(statement, field, None)
        if block:
            blocks.append(block)
    for handler in getattr(statement, "handlers", []):
        blocks.append(handler.body)
    for case in getattr(statement, "cases", []):
        blocks.append(case.body)
    return blocks
