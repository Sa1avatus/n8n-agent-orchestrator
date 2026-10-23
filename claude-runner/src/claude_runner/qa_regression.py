"""Parsers of regression tool output into stable failure keys.

A key names a failure without its line number, so a line that moves keeps its key: pytest and
node give ``path::name``, ruff ``path:code``, mypy ``path:code:message``. Every function is pure:
it takes the captured text and returns the sorted unique keys.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LAYER = "regression"

PYTEST_FAILURE = re.compile(r"^(?:FAILED|ERROR) (.+?)(?: - |$)")
NODE_NOT_OK = re.compile(r"^(\s*)not ok \d+ - (.*?)( # (?:TODO|SKIP)\b.*)?$")
NODE_TEST_LINE = re.compile(r"^\s*(?:not )?ok \d+ ")
NODE_LOCATION = re.compile(r"^\s*location: '(.*):\d+:\d+'$")
RUFF_CODE = re.compile(r"^([A-Z]+[0-9]+) ")
RUFF_LOCATION = re.compile(r"^\s*--> (.+?):\d+:\d+$")
MYPY_ERROR = re.compile(r"^(.+?):\d+: error: (.*?)(?:\s+\[([\w-]+)\])?$")


def parse_pytest(output: str) -> list[str]:
    keys = {match.group(1) for line in output.splitlines() if (match := PYTEST_FAILURE.match(line))}
    return sorted(keys)


def parse_node(output: str, project_root: str) -> list[str]:
    lines = output.splitlines()
    keys: set[str] = set()
    for index, line in enumerate(lines):
        match = NODE_NOT_OK.match(line)
        if match is None or match.group(3):
            continue
        file, is_suite = _node_block(lines, index, match.group(1), project_root)
        if not is_suite:
            name = match.group(2).strip()
            keys.add(f"{file}::{name}" if file else name)
    return sorted(keys)


def _node_block(lines: list[str], start: int, indent: str, project_root: str) -> tuple[str, bool]:
    file = ""
    is_suite = False
    for line in lines[start + 1 :]:
        if line == indent + "  ..." or NODE_TEST_LINE.match(line):
            break
        location = NODE_LOCATION.match(line)
        if location is not None:
            file = _relative(location.group(1), project_root)
        is_suite = is_suite or line.strip() == "type: 'suite'"
    return file, is_suite


def parse_ruff(output: str) -> list[str]:
    keys: set[str] = set()
    code = ""
    for line in output.splitlines():
        found = RUFF_CODE.match(line)
        if found is not None:
            code = found.group(1)
            continue
        location = RUFF_LOCATION.match(line)
        if location is not None and code:
            keys.add(f"{_slashes(location.group(1))}:{code}")
            code = ""
    return sorted(keys)


def parse_mypy(output: str) -> list[str]:
    keys: set[str] = set()
    for line in output.splitlines():
        match = MYPY_ERROR.match(line)
        if match is not None:
            path, message, code = match.group(1), match.group(2), match.group(3) or ""
            keys.add(f"{_slashes(path)}:{code}:{message}")
    return sorted(keys)


def _slashes(path: str) -> str:
    return path.replace("\\", "/")


def _relative(path: str, project_root: str) -> str:
    path = _slashes(path)
    prefix = _slashes(project_root).rstrip("/") + "/"
    return path[len(prefix) :] if path.startswith(prefix) else path


def new_failures(
    found: Mapping[str, Sequence[str]], baseline: Mapping[str, Sequence[str]]
) -> dict[str, list[str]]:
    fresh = {tool: sorted(set(keys) - set(baseline.get(tool, ()))) for tool, keys in found.items()}
    return {tool: keys for tool, keys in fresh.items() if keys}


def regression_verdict(
    *,
    has_profile: bool,
    found: Mapping[str, Sequence[str]],
    baseline: Mapping[str, Sequence[str]],
    blocked_reason: str | None = None,
    timed_out: bool = False,
) -> dict[str, Any]:
    reason: str | None = None
    fresh = new_failures(found, baseline)
    if not has_profile:
        outcome, reason = "skipped", "no QA profile"
    elif blocked_reason is not None:
        outcome, reason = "blocked", blocked_reason
    elif timed_out:
        outcome, reason = "timeout", "the regression layer timed out"
    elif fresh:
        tool = min(fresh)
        outcome, reason = "fail", f"new failure {fresh[tool][0]}"
    else:
        outcome = "pass"
    verdict: dict[str, Any] = {"layer": LAYER, "scenario": LAYER, "verdict": outcome}
    if reason is not None:
        verdict["reason"] = reason
    return verdict


PROFILE_FILE_KEYS = frozenset({"projects"})
PROFILE_KEYS = frozenset({"name", "working_directory", "regression", "baseline"})
COMMAND_KEYS = frozenset({"name", "command"})
DRIVE = re.compile(r"^(?:/([A-Za-z])(?=/|$)|([A-Za-z]):(?=/|$))")


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class RegressionCommand:
    name: str
    command: str


@dataclass(frozen=True)
class Profile:
    name: str
    working_directory: str
    regression: tuple[RegressionCommand, ...]
    baseline: str


def load_profiles(path: Path) -> list[Profile]:
    import yaml  # the 'qa' extra, read the same way as qa_runner.parse_scenario

    try:
        data: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileError(f"invalid YAML in {path}: {exc}") from exc
    entries = _mapping(data, "projects file", PROFILE_FILE_KEYS)["projects"]
    if not isinstance(entries, list) or not entries:
        raise ProfileError("projects must be a non-empty list")
    profiles = [_profile(entry) for entry in entries]
    names = [profile.name for profile in profiles]
    if len(set(names)) != len(names):
        raise ProfileError(f"duplicate project name in {path}")
    return profiles


def _profile(entry: object) -> Profile:
    data = _mapping(entry, "project", PROFILE_KEYS)
    name = _text(data["name"], "project name")
    where = f"project {name}"
    return Profile(
        name=name,
        working_directory=_text(data["working_directory"], f"{where} working_directory"),
        regression=_commands(data["regression"], where),
        baseline=_text(data["baseline"], f"{where} baseline"),
    )


def _commands(value: object, where: str) -> tuple[RegressionCommand, ...]:
    if not isinstance(value, list) or not value:
        raise ProfileError(f"{where} regression must be a non-empty list")
    commands = tuple(_command(entry, where) for entry in value)
    names = [command.name for command in commands]
    if len(set(names)) != len(names):
        raise ProfileError(f"{where} repeats a regression name")
    return commands


def _command(entry: object, where: str) -> RegressionCommand:
    data = _mapping(entry, f"{where} regression entry", COMMAND_KEYS)
    return RegressionCommand(
        name=_text(data["name"], f"{where} regression name"),
        command=_text(data["command"], f"{where} regression command"),
    )


def _mapping(value: object, what: str, keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ProfileError(f"{what} must have exactly the keys {sorted(keys)}")
    return value


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileError(f"{what} must be a non-empty string")
    return value


def find_profile(profiles: Sequence[Profile], working_directory: str) -> Profile | None:
    target = _normalised(working_directory)
    matches = [
        profile
        for profile in profiles
        if _is_within(target, _normalised(profile.working_directory))
    ]
    return max(
        matches,
        key=lambda profile: len(_normalised(profile.working_directory)),
        default=None,
    )


def _normalised(path: str) -> str:
    path = path.replace("\\", "/").rstrip("/")
    match = DRIVE.match(path)
    if match is None:
        return path
    letter = match.group(1) or match.group(2)
    return f"/{letter.lower()}{path[match.end() :]}"


def _is_within(path: str, folder: str) -> bool:
    return path == folder or path.startswith(folder + "/")


def read_baseline(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    data: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not all(
        isinstance(tool, str)
        and isinstance(keys, list)
        and all(isinstance(key, str) for key in keys)
        for tool, keys in data.items()
    ):
        raise ValueError(f"baseline {path} must map tool names to lists of failure keys")
    return {tool: list(keys) for tool, keys in data.items()}


def write_baseline(path: Path, baseline: Mapping[str, Sequence[str]]) -> None:
    sorted_baseline = {tool: sorted(set(keys)) for tool, keys in baseline.items()}
    text = json.dumps(sorted_baseline, indent=2, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
