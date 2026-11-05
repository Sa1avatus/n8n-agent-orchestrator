"""Tests for the ast complexity gate: measurement and baseline comparison."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ahawr_runner.qa_complexity import (
    RUFF_TIMEOUT_SECONDS,
    ComplexityError,
    check_c901,
    find_violations,
    function_length_lines,
    function_nesting_depth,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "qa_complexity"


def measured_for(file: str) -> dict[str, dict[str, dict[str, int]]]:
    """The measured shape of one fixture file, built from the two measures."""
    lengths = function_length_lines(FIXTURES / file)
    depths = function_nesting_depth(FIXTURES / file)
    return {
        file: {
            name: {"length_lines": lengths[name], "nesting_depth": depths[name]}
            for name in lengths
        }
    }


def test_measures_length_and_depth_of_every_def() -> None:
    lengths = function_length_lines(FIXTURES / "nested_blocks.py")
    depths = function_nesting_depth(FIXTURES / "nested_blocks.py")
    assert lengths == {"outer": 10, "inner": 2}
    assert depths == {"outer": 3, "inner": 0}


def test_a_value_equal_to_the_baseline_passes() -> None:
    baseline: dict[str, dict[str, dict[str, int]]] = {
        "grown_function.py": {"count_marks": {"length_lines": 7, "nesting_depth": 2}}
    }
    violations = find_violations(
        measured_for("grown_function.py"), baseline, {}, {"grown_function.py"}
    )
    assert violations == []


def test_a_worse_value_fails_with_file_function_value_and_baseline() -> None:
    baseline: dict[str, dict[str, dict[str, int]]] = {
        "grown_function.py": {"count_marks": {"length_lines": 6, "nesting_depth": 1}}
    }
    violations = find_violations(
        measured_for("grown_function.py"), baseline, {}, {"grown_function.py"}
    )
    assert violations == [
        {
            "file": "grown_function.py",
            "function": "count_marks",
            "metric": "length_lines",
            "value": 7,
            "baseline": 6,
        },
        {
            "file": "grown_function.py",
            "function": "count_marks",
            "metric": "nesting_depth",
            "value": 2,
            "baseline": 1,
        },
    ]


def test_a_saved_baseline_json_is_compared_as_saved() -> None:
    baseline: dict[str, Any] = json.loads(
        (FIXTURES / "baseline.json").read_text(encoding="utf-8")
    )
    violations = find_violations(
        measured_for("grown_function.py"), baseline, {}, {"grown_function.py"}
    )
    assert violations == []


def test_a_new_function_over_the_threshold_fails() -> None:
    violations = find_violations(
        measured_for("new_function.py"),
        {},
        {"length_lines": 5, "nesting_depth": 1},
        {"new_function.py"},
    )
    assert violations == [
        {
            "file": "new_function.py",
            "function": "search_words",
            "metric": "length_lines",
            "value": 6,
            "baseline": 5,
        },
        {
            "file": "new_function.py",
            "function": "search_words",
            "metric": "nesting_depth",
            "value": 2,
            "baseline": 1,
        },
    ]


def test_a_new_function_at_the_threshold_passes() -> None:
    violations = find_violations(
        measured_for("new_function.py"),
        {},
        {"length_lines": 6, "nesting_depth": 2},
        {"new_function.py"},
    )
    assert violations == []


def test_an_untouched_file_is_never_violated() -> None:
    baseline: dict[str, dict[str, dict[str, int]]] = {
        "grown_function.py": {"count_marks": {"length_lines": 1, "nesting_depth": 0}}
    }
    violations = find_violations(measured_for("grown_function.py"), baseline, {}, set())
    assert violations == []


def test_fake_ruff_output_is_parsed_into_a_violation_against_the_threshold() -> None:
    fake_ruff = FIXTURES / "fake_ruff.py"
    result = check_c901(
        [str(FIXTURES / "grown_function.py")],
        max_complexity=5,
        ruff_bin=str(fake_ruff),
    )
    assert result == {
        "verdict": "FAIL",
        "violations": [
            {
                "file": str(FIXTURES / "grown_function.py"),
                "function": "count_marks",
                "metric": "complexity",
                "value": 7,
                "baseline": 5,
            }
        ],
        "reason": None,
    }


def test_a_c901_value_equal_to_the_saved_baseline_is_not_worse() -> None:
    result = check_c901(
        [str(FIXTURES / "grown_function.py")],
        max_complexity=5,
        baseline={str(FIXTURES / "grown_function.py"): {"count_marks": {"complexity": 7}}},
        ruff_bin=str(FIXTURES / "fake_ruff.py"),
    )
    assert result["verdict"] == "PASS"
    assert result["violations"] == []


def test_a_missing_ruff_gives_the_verdict_not_run_with_the_reason() -> None:
    result = check_c901([str(FIXTURES / "grown_function.py")], 5, ruff_bin="ruff-not-installed")
    assert result["verdict"] == "NOT RUN"
    assert "not found" in result["reason"]


def test_ruff_runs_with_the_c901_select_and_the_timeout_constant(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(command, 0, "[]", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = check_c901(["a.py"], 7)
    assert result["verdict"] == "PASS"
    assert captured["timeout"] == RUFF_TIMEOUT_SECONDS
    assert "--select" in captured["command"] and "C901" in captured["command"]
    assert "--output-format" in captured["command"] and "json" in captured["command"]


@pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff is not installed")
def test_installed_ruff_measures_the_known_complexity_of_a_fixture() -> None:
    # nested_blocks.py: outer() scores 5 (try>if>while, plus the except body's def).
    file = str(FIXTURES / "nested_blocks.py")
    result = check_c901([file], max_complexity=1)
    assert result["verdict"] == "FAIL"
    assert result["violations"] == [
        {"file": file, "function": "outer", "metric": "complexity", "value": 5, "baseline": 1}
    ]
    # The saved complexity is not a violation when the baseline holds it.
    passed = check_c901([file], max_complexity=1, baseline={file: {"outer": {"complexity": 5}}})
    assert passed["verdict"] == "PASS"


def test_an_empty_file_list_is_not_run() -> None:
    result = check_c901([], 5)
    assert result["verdict"] == "NOT RUN"
    assert "no files" in result["reason"]


def test_a_syntax_error_file_raises_a_clear_error() -> None:
    # shortcut: the fixture is .txt so `ruff check src tests` of the QA gate can
    # still lint the fixture directory; switch to .py when the gate excludes fixtures.
    for measure in (function_length_lines, function_nesting_depth):
        with pytest.raises(ComplexityError) as raised:
            measure(FIXTURES / "broken_syntax.txt")
        message = str(raised.value)
        assert "broken_syntax.txt" in message
        assert "syntax error on line 2" in message
