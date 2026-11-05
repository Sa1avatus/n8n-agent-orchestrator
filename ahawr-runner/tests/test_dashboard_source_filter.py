from __future__ import annotations

from ahawr_runner.dashboard import _runs_from

RUNS = [
    {"run_id": "a", "source": "claude-code"},
    {"run_id": "b", "source": "codex"},
    {"run_id": "c", "source": "hermes"},
]


def test_no_source_keeps_every_run() -> None:
    assert _runs_from(RUNS, []) == RUNS
    assert _runs_from(RUNS, [""]) == RUNS


def test_a_source_value_keeps_the_runs_of_that_source() -> None:
    assert [r["run_id"] for r in _runs_from(RUNS, ["codex"])] == ["b"]
    assert [r["run_id"] for r in _runs_from(RUNS, ["claude-code"])] == ["a"]


def test_several_values_are_ored() -> None:
    assert [r["run_id"] for r in _runs_from(RUNS, ["codex", "hermes"])] == ["b", "c"]
