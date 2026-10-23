from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from claude_runner.qa_regression import (
    Profile,
    ProfileError,
    find_profile,
    load_profiles,
    new_failures,
    parse_mypy,
    parse_node,
    parse_pytest,
    parse_ruff,
    read_baseline,
    regression_verdict,
    write_baseline,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "qa_regression"
PROJECTS_FILE = Path(__file__).resolve().parents[1] / "qa" / "projects.yaml"
PROFILE_YAML = """\
projects:
  - name: claude-runner
    working_directory: /d/n8n/claude-runner
    regression:
      - name: pytest
        command: python -m pytest -q --no-cov -p no:cacheprovider -rf
    baseline: qa/baselines/claude-runner.json
"""
# The directory the node fixture was recorded under; its location lines start with it.
NODE_RECORDED_ROOT = "/tmp/qa_rec/node"
CASES = json.loads(
    (Path(__file__).resolve().parent / "qa_regression_cases.json").read_text(encoding="utf-8")
)["cases"]


def _verdict_for(case: dict[str, Any]) -> dict[str, Any]:
    return regression_verdict(
        has_profile=case.get("has_profile", True),
        found=case["found"],
        baseline=case["baseline"],
        blocked_reason=case.get("blocked_reason"),
        timed_out=case.get("timed_out", False),
    )


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_pytest_failures_become_test_ids() -> None:
    assert parse_pytest(_read("pytest_fail.txt")) == [
        "tests/test_sample.py::test_fails",
        "tests/test_sample.py::test_param[2]",
    ]


def test_pytest_clean_output_gives_empty_set() -> None:
    assert parse_pytest(_read("pytest_clean.txt")) == []


def test_pytest_error_lines_count_as_failures() -> None:
    output = "ERROR tests/test_x.py::test_y - fixture 'db' not found\n"
    assert parse_pytest(output) == ["tests/test_x.py::test_y"]


def test_node_failure_is_keyed_by_file_and_name() -> None:
    assert parse_node(_read("node_fail.txt"), NODE_RECORDED_ROOT) == [
        "a.test.js::subtracts wrongly",
    ]


def test_node_clean_output_gives_empty_set() -> None:
    assert parse_node(_read("node_clean.txt"), NODE_RECORDED_ROOT) == []


def test_node_todo_failure_is_not_a_failure() -> None:
    output = "not ok 1 - known bug # TODO wrong result\n"
    assert parse_node(output, NODE_RECORDED_ROOT) == []


def test_ruff_errors_are_keyed_by_path_and_code_without_line() -> None:
    assert parse_ruff(_read("ruff_fail.txt")) == [
        "src/pkg/bad.py:F401",
        "src/pkg/second_unused.py:F401",
    ]


def test_ruff_clean_output_gives_empty_set() -> None:
    assert parse_ruff(_read("ruff_clean.txt")) == []


def test_mypy_errors_are_keyed_by_path_code_and_message_without_line() -> None:
    assert parse_mypy(_read("mypy_fail.txt")) == [
        "src/pkg/bad.py:no-untyped-def:Function is missing a type annotation",
        'src/pkg/types_bad.py:arg-type:Argument 1 to "add" has incompatible type "str"; '
        'expected "int"',
        "src/pkg/types_bad.py:assignment:Incompatible types in assignment "
        '(expression has type "str", variable has type "int")',
    ]


def test_mypy_clean_output_gives_empty_set() -> None:
    assert parse_mypy(_read("mypy_clean.txt")) == []


def test_mypy_key_does_not_change_when_the_line_moves() -> None:
    before = 'src/a.py:3: error: Argument 1 has type "str"  [arg-type]\n'
    after = 'src/a.py:30: error: Argument 1 has type "str"  [arg-type]\n'
    assert parse_mypy(before) == parse_mypy(after)


def test_new_failure_is_a_key_missing_from_the_baseline() -> None:
    found = {"pytest": ["a::one", "a::two"], "ruff": ["b.py:F401"]}
    baseline = {"pytest": ["a::one"], "ruff": ["b.py:F401"]}
    assert new_failures(found, baseline) == {"pytest": ["a::two"]}


def test_tool_missing_from_the_baseline_has_only_new_failures() -> None:
    assert new_failures({"node": ["x.js::t"]}, {}) == {"node": ["x.js::t"]}


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_regression_verdict_table(case: dict[str, Any]) -> None:
    verdict = _verdict_for(case)
    assert verdict["layer"] == "regression"
    assert verdict["verdict"] == case["expect"]["verdict"]
    assert verdict.get("reason") == case["expect"].get("reason")


def test_only_a_new_failure_gives_fail() -> None:
    for case in CASES:
        verdict = _verdict_for(case)
        if verdict["verdict"] == "fail":
            assert new_failures(case["found"], case["baseline"])
            assert verdict["reason"].startswith("new failure ")


def test_timeout_wins_over_new_failures() -> None:
    verdict = regression_verdict(
        has_profile=True,
        found={"pytest": ["a::b"]},
        baseline={},
        timed_out=True,
    )
    assert verdict["verdict"] == "timeout"


def _profiles(tmp_path: Path, text: str = PROFILE_YAML) -> list[Profile]:
    path = tmp_path / "projects.yaml"
    path.write_text(text, encoding="utf-8")
    return load_profiles(path)


def test_repository_profile_has_the_claude_runner_commands() -> None:
    profiles = load_profiles(PROJECTS_FILE)
    assert [profile.name for profile in profiles] == ["claude-runner"]
    profile = profiles[0]
    assert [(command.name, command.command) for command in profile.regression] == [
        ("pytest", "python -m pytest -q --no-cov -p no:cacheprovider -rf"),
        ("node", "node --test tests/*.test.js"),
        ("ruff", "ruff check src tests"),
        ("mypy", "mypy src"),
    ]
    assert profile.baseline == "qa/baselines/claude-runner.json"


def test_matching_prefix_finds_the_profile(tmp_path: Path) -> None:
    profile = find_profile(_profiles(tmp_path), "/d/n8n/claude-runner/src")
    assert profile is not None
    assert profile.name == "claude-runner"


@pytest.mark.parametrize(
    "directory",
    ["/d/n8n/other", "/d/n8n/claude-runner2", "/d/n8n"],
    ids=["other", "sibling", "parent"],
)
def test_no_matching_prefix_gives_none(tmp_path: Path, directory: str) -> None:
    assert find_profile(_profiles(tmp_path), directory) is None


@pytest.mark.parametrize(
    "directory",
    ["D:\\n8n\\claude-runner\\src", "d:/n8n/claude-runner/src", "/D/n8n/claude-runner/src"],
)
def test_windows_path_matches_posix_profile(tmp_path: Path, directory: str) -> None:
    profile = find_profile(_profiles(tmp_path), directory)
    assert profile is not None
    assert profile.name == "claude-runner"


def test_posix_path_matches_windows_profile(tmp_path: Path) -> None:
    text = PROFILE_YAML.replace("/d/n8n/claude-runner", "D:\\n8n\\claude-runner")
    profile = find_profile(_profiles(tmp_path, text), "/d/n8n/claude-runner/src")
    assert profile is not None
    assert profile.name == "claude-runner"


INVALID_PROFILES = {
    "empty-projects": "projects: []\n",
    "missing-baseline": PROFILE_YAML.replace("    baseline: qa/baselines/claude-runner.json\n", ""),
    "unknown-key": PROFILE_YAML + "    owner: someone\n",
    "missing-command": PROFILE_YAML.replace(
        "        command: python -m pytest -q --no-cov -p no:cacheprovider -rf\n", ""
    ),
    "empty-command": PROFILE_YAML.replace(
        "python -m pytest -q --no-cov -p no:cacheprovider -rf", "  "
    ),
    "repeated-tool": PROFILE_YAML.replace(
        "    baseline", "      - name: pytest\n        command: echo\n    baseline"
    ),
    "not-yaml": "projects: [\n",
}


@pytest.mark.parametrize("text", INVALID_PROFILES.values(), ids=list(INVALID_PROFILES))
def test_invalid_profile_is_rejected(tmp_path: Path, text: str) -> None:
    with pytest.raises(ProfileError):
        _profiles(tmp_path, text)


def test_baseline_write_then_read_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "baselines" / "claude-runner.json"
    write_baseline(path, {"ruff": ["b.py:F401", "a.py:E501"], "pytest": ["t::b", "t::a", "t::a"]})
    assert read_baseline(path) == {
        "pytest": ["t::a", "t::b"],
        "ruff": ["a.py:E501", "b.py:F401"],
    }
    assert list(json.loads(path.read_text(encoding="utf-8"))) == ["pytest", "ruff"]
    assert [entry.name for entry in path.parent.iterdir()] == ["claude-runner.json"]


def test_missing_baseline_reads_as_empty(tmp_path: Path) -> None:
    assert read_baseline(tmp_path / "absent.json") == {}
