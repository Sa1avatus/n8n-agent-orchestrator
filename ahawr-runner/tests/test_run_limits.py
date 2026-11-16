"""Tests of run_limits: the optional per-role shortening of the run time limit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ahawr_runner.run_limits import OVERRIDE_FILE_NAME, effective_run_limit_seconds

MAX_SECONDS = 3600


def _write(directory: Path, content: str) -> None:
    (directory / OVERRIDE_FILE_NAME).write_text(content, encoding="utf-8")


def test_without_a_file_the_runner_limit_stays(tmp_path: Path) -> None:
    assert effective_run_limit_seconds(tmp_path, "worker", MAX_SECONDS) == MAX_SECONDS


def test_the_override_shortens_only_the_named_role(tmp_path: Path) -> None:
    _write(tmp_path, json.dumps({"worker": 60}))
    assert effective_run_limit_seconds(tmp_path, "worker", MAX_SECONDS) == 60
    assert effective_run_limit_seconds(tmp_path, "architect", MAX_SECONDS) == MAX_SECONDS
    assert effective_run_limit_seconds(tmp_path, "reviewer", MAX_SECONDS) == MAX_SECONDS


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[]",
        json.dumps({"worker": 0}),
        json.dumps({"worker": -5}),
        json.dumps({"worker": 7200}),
        json.dumps({"worker": 3600}),
        json.dumps({"worker": "60"}),
        json.dumps({"worker": True}),
    ],
)
def test_an_invalid_or_longer_value_changes_nothing(tmp_path: Path, content: str) -> None:
    _write(tmp_path, content)
    assert effective_run_limit_seconds(tmp_path, "worker", MAX_SECONDS) == MAX_SECONDS


def test_removing_the_file_restores_the_limit(tmp_path: Path) -> None:
    _write(tmp_path, json.dumps({"worker": 60}))
    assert effective_run_limit_seconds(tmp_path, "worker", MAX_SECONDS) == 60
    (tmp_path / OVERRIDE_FILE_NAME).unlink()
    assert effective_run_limit_seconds(tmp_path, "worker", MAX_SECONDS) == MAX_SECONDS
