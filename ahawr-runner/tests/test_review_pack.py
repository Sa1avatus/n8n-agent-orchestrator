"""review_pack.py (T002B): changed files grouped by risk tier, checks ran and not ran.

The script is run as a subprocess against a fixture of stored run results: per-run
event logs under fixtures/review_pack/risk-tiers/events/ and a runner.sqlite seeded
with the mission's run rows. The script is never imported, and the fixture is
verified byte-identical after a run.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "review_pack" / "risk-tiers"
MISSION_ID = "risk-tiers"
REPO_ROOT = Path(__file__).resolve().parents[2]
PACK_SCRIPT = REPO_ROOT / "data-tables" / "ops" / "review_pack.py"
SUBPROCESS_TIMEOUT_SECONDS = 60
# the subprocess must import the source tree, not the stale pip-installed copy,
# the same way conftest.py:16-18 does for the test process itself
RUNNER_SRC = str(REPO_ROOT / "ahawr-runner" / "src")

_SEED_RUNS = (
    (
        "run_a1b2c3",
        "worker",
        "/d/n8n",
        "MISSION: risk-tiers TASK T002B first attempt",
        "2026-11-02T10:00:00+00:00",
    ),
    (
        "run_b2c3d4",
        "worker",
        "/d/n8n",
        "MISSION: risk-tiers TASK T002B retry after review",
        "2026-11-02T11:00:00+00:00",
    ),
    (
        "run_c3d4e5",
        "reviewer",
        "/d/n8n",
        "MISSION: risk-tiers TASK T002B review",
        "2026-11-02T12:00:00+00:00",
    ),
)


def _ensure_fixture_db() -> None:
    """Seed runner.sqlite with the mission's run rows, once (the logs stay as-is)."""
    if FIXTURE_DIR.joinpath("runner.sqlite").exists():
        return
    conn = sqlite3.connect(FIXTURE_DIR / "runner.sqlite")
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS runs ("
            "run_id TEXT PRIMARY KEY, role TEXT, cwd TEXT, input TEXT, created_at TEXT)"
        )
        conn.executemany(
            "INSERT INTO runs (run_id, role, cwd, input, created_at) VALUES (?, ?, ?, ?, ?)",
            _SEED_RUNS,
        )
        conn.commit()
    finally:
        conn.close()


_ensure_fixture_db()


def _run_pack(*mission_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PACK_SCRIPT), *mission_args, "--data-dir", str(FIXTURE_DIR)],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
        env={**os.environ, "PYTHONPATH": RUNNER_SRC},
    )


def _fixture_snapshot() -> str:
    """sha256 over every fixture file name and content (the read-only guarantee)."""
    digest = hashlib.sha256()
    for path in sorted(FIXTURE_DIR.rglob("*")):
        if path.is_file():
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _section_lines(output: str, heading: str) -> list[str]:
    section = output.split(heading, 1)[1].split("\n\n")[0]
    return [line.strip() for line in section.splitlines() if line.strip()]


def test_pack_groups_changed_files_by_tier_and_reports_checks() -> None:
    result = _run_pack(MISSION_ID)
    assert result.returncode == 0, result.stderr
    core = _section_lines(result.stdout, "Core files:")
    supporting = _section_lines(result.stdout, "Supporting files:")
    # data-tables/ops/review_pack.py matches no pattern -> core (unmatched);
    # runs.py is core by an explicit pattern; the retry run edits review_pack.py
    # again and the file appears once.
    assert core == [
        "data-tables/ops/review_pack.py",
        "ahawr-runner/src/ahawr_runner/risk_tiers.py",
        "ahawr-runner/src/ahawr_runner/runs.py",
    ]
    assert supporting == [
        "ahawr-runner/tests/test_review_pack.py",
        "ahawr-runner/docs/review_pack_notes.md",
    ]
    checks = _section_lines(result.stdout, "Checks:")
    assert any(line.startswith("pytest: passed (run_a1b2c3)") for line in checks)
    assert any(line.startswith("pytest: failed (run_b2c3d4)") for line in checks)
    assert any(line.startswith("ruff: passed (run_a1b2c3)") for line in checks)
    assert any(line.startswith("mypy: no stored result (run_b2c3d4)") for line in checks)
    assert any(line.startswith("mypy: passed (run_c3d4e5)") for line in checks)
    assert "did not run: node" in result.stdout
    assert "7 passed in 0.42s" in result.stdout
    assert "All checks passed!" in result.stdout
    assert "Success: no issues found in 1 source file" in result.stdout


def test_pack_never_touches_the_fixture() -> None:
    before = _fixture_snapshot()
    assert _run_pack(MISSION_ID).returncode == 0
    assert _fixture_snapshot() == before


def test_pack_with_unknown_mission_exits_with_a_clear_message() -> None:
    result = _run_pack("ghost-mission")
    assert result.returncode == 2
    assert "mission ghost-mission not found" in result.stderr
    assert not result.stdout


def test_pack_without_a_mission_id_exits_with_usage() -> None:
    result = _run_pack()
    assert result.returncode == 2
    assert "usage" in result.stderr.lower()


def test_pack_with_a_missing_results_dir_exits() -> None:
    missing_dir = FIXTURE_DIR.parent / "no-such-results"
    result = subprocess.run(
        [sys.executable, str(PACK_SCRIPT), MISSION_ID, "--data-dir", str(missing_dir)],
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
        env={**os.environ, "PYTHONPATH": RUNNER_SRC},
    )
    assert result.returncode == 2
    assert "no stored run results" in result.stderr
