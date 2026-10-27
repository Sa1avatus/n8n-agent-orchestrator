"""Tests for report_facts.py (the facts pack of the mission report): criteria numbering,
the task table with 'no evidence' for missing values, remarks cut at 300 characters for
unaccepted tasks only, known unmet from the stored status (an operator-accept event counts
as accepted), and ValueError on a malformed digest. The digest is made up, modelled on a
mission whose tasks T001-T016 are accepted and T017 (the report task) is not accepted. It also
covers the check-line extraction, redaction of made-up secrets, the size cut and the git reader."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from claude_runner.provider_secrets import REDACTED_TEXT
from claude_runner.report_facts import (
    MAX_PACK_CHARS,
    NO_EVIDENCE,
    PACK_END,
    PACK_HEADER,
    RepoFacts,
    build_pack,
    collect_repo_facts,
    extract_check_lines,
    known_unmet,
    parse_digest,
    render_criteria,
    render_task_table,
    unresolved_remarks,
)

LONG_REMARK = "Reviewer remark about the report. " * 30


def _raw_task(task_id: str, status: str, **overrides: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": task_id,
        "title": f"Title of {task_id}",
        "status": status,
        "attempts": 1,
        "last_score": 90.0,
        "files_changed": ["src/module.py"],
        "last_review_text": None,
    }
    raw.update(overrides)
    return raw


def _raw_digest(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "mission_id": "m-example",
        "mission_name": "Example mission",
        "acceptance_criteria": ["The first thing works.", "The second thing is documented."],
        "tasks": tasks,
        "validation_output": "",
        "operator_accept_events": [],
    }


def _mission_digest() -> dict[str, Any]:
    tasks = [_raw_task(f"T{number:03d}", "accepted") for number in range(1, 17)]
    tasks.append(
        _raw_task(
            "T017",
            "not_accepted",
            title="Mission report",
            attempts=3,
            last_score=38.0,
            files_changed=None,
            last_review_text=LONG_REMARK,
        )
    )
    return _raw_digest(tasks)


def _table_rows(raw: dict[str, Any]) -> list[str]:
    return render_task_table(parse_digest(raw)).splitlines()[2:]


def test_criteria_are_numbered_from_one() -> None:
    assert render_criteria(["first", "second"]) == "1. first\n2. second"
    assert render_criteria([]) == NO_EVIDENCE


def test_task_table_has_one_row_per_task_and_computed_accepted_total() -> None:
    raw = _mission_digest()
    rows = _table_rows(raw)
    accepted_total = sum(1 for task in raw["tasks"] if task["status"] == "accepted")
    assert len(rows) == len(raw["tasks"])
    assert sum(1 for row in rows if "| accepted |" in row) == accepted_total
    assert rows[0] == "| T001 | Title of T001 | accepted | 1 | 90 | src/module.py |"
    assert rows[-1] == "| T017 | Mission report | not_accepted | 3 | 38 | no evidence |"


def test_missing_values_render_as_no_evidence() -> None:
    raw = _raw_digest(
        [
            _raw_task(
                "T099",
                "not_accepted",
                title=None,
                attempts=None,
                last_score=None,
                files_changed=None,
            )
        ]
    )
    assert _table_rows(raw) == [
        "| T099 | no evidence | not_accepted | no evidence | no evidence | no evidence |"
    ]


def test_remark_is_cut_at_300_characters_for_unaccepted_task() -> None:
    remarks = unresolved_remarks(parse_digest(_mission_digest()))
    assert len(LONG_REMARK) > 300
    assert remarks == [("T017", LONG_REMARK[:300])]
    assert len(remarks[0][1]) == 300


def test_remarks_are_only_for_unaccepted_tasks() -> None:
    raw = _raw_digest(
        [
            _raw_task("T001", "accepted", last_review_text="Old remark"),
            _raw_task("T002", "not_accepted", last_review_text="Open remark"),
        ]
    )
    assert unresolved_remarks(parse_digest(raw)) == [("T002", "Open remark")]


def test_missing_remark_text_is_no_evidence() -> None:
    raw = _raw_digest([_raw_task("T002", "not_accepted", last_review_text=None)])
    assert unresolved_remarks(parse_digest(raw)) == [("T002", NO_EVIDENCE)]


def test_known_unmet_follows_the_stored_status() -> None:
    assert known_unmet(parse_digest(_mission_digest())) == [
        "T017 Mission report: not_accepted"
    ]


def test_operator_accept_event_counts_as_accepted() -> None:
    raw = _mission_digest()
    raw["operator_accept_events"] = [{"task_id": "T017"}]
    digest = parse_digest(raw)
    assert known_unmet(digest) == []
    assert unresolved_remarks(digest) == []
    assert _table_rows(raw)[-1] == "| T017 | Mission report | accepted | 3 | 38 | no evidence |"


def test_status_is_not_read_from_review_text() -> None:
    raw = _raw_digest(
        [_raw_task("T001", "accepted", last_review_text="not accepted, rework needed")]
    )
    assert known_unmet(parse_digest(raw)) == []


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param([], id="digest is not an object"),
        pytest.param({**_raw_digest([]), "tasks": {"id": "T001"}}, id="tasks is not a list"),
        pytest.param(
            _raw_digest([{**_raw_task("T001", "accepted"), "id": None}]),
            id="task id is null",
        ),
        pytest.param(
            _raw_digest([_raw_task("T001", "accepted", attempts=-1)]),
            id="negative attempts",
        ),
        pytest.param(
            _raw_digest([_raw_task("T001", "accepted", attempts=True)]),
            id="boolean attempts",
        ),
        pytest.param(
            {**_raw_digest([]), "operator_accept_events": None},
            id="operator_accept_events is null",
        ),
        pytest.param(
            {**_raw_digest([]), "acceptance_criteria": "one string"},
            id="criteria is a string",
        ),
    ],
)
def test_malformed_digest_raises_value_error(raw: Any) -> None:
    with pytest.raises(ValueError):
        parse_digest(raw)


_SAMPLE_PYTEST = "pytest tests/test_x.py\n5 passed, 1 failed in 0.42s\n"
_SAMPLE_RUFF = "ruff check src\nFound 3 errors.\n"
_SAMPLE_MYPY = "mypy src\nSuccess: no issues found in 12 source files\n"
_NO_REPO_FACTS: RepoFacts = {
    "diff_stat_uncommitted": None,
    "diff_stat_committed": None,
    "untracked_files": None,
}


def test_check_lines_are_quoted_per_tool() -> None:
    assert extract_check_lines(_SAMPLE_PYTEST + _SAMPLE_RUFF + _SAMPLE_MYPY) == [
        "5 passed, 1 failed in 0.42s",
        "Found 3 errors.",
        "Success: no issues found in 12 source files",
    ]


def test_missing_check_output_is_no_evidence() -> None:
    assert extract_check_lines("no tool output here") == [
        "pytest: no evidence",
        "ruff: no evidence",
        "mypy: no evidence",
    ]
    assert extract_check_lines(_SAMPLE_RUFF) == [
        "pytest: no evidence",
        "Found 3 errors.",
        "mypy: no evidence",
    ]


def test_build_pack_redacts_made_up_secrets() -> None:
    secrets = (
        "sk-fakeexamplekey0123456789",
        "hunter2-example",
        "fake.Token-Value.0123",
        "fake-pass-0002",
        "fake-key-0001",
    )
    raw = _raw_digest(
        [
            _raw_task(
                "T001",
                "not_accepted",
                title="Calls sk-fakeexamplekey0123456789",
                last_review_text=(
                    "Config has PASSWORD=hunter2-example; header is Bearer fake.Token-Value.0123"
                ),
            )
        ]
    )
    raw["validation_output"] = "pytest: 1 passed, api_key=fake-key-0001"
    repo_facts: RepoFacts = {
        **_NO_REPO_FACTS,
        "untracked_files": ["postgresql://appuser:fake-pass-0002@db.example.test/app"],
    }
    pack = build_pack(parse_digest(raw), repo_facts)
    for secret in secrets:
        assert secret not in pack
    assert REDACTED_TEXT in pack


def test_oversized_pack_loses_remarks_first_and_keeps_table_and_criteria() -> None:
    raw = _raw_digest(
        [
            _raw_task(f"T{number:03d}", "not_accepted", last_review_text=LONG_REMARK)
            for number in range(1, 41)
        ]
    )
    digest = parse_digest(raw)
    pack = build_pack(digest, _NO_REPO_FACTS)
    lines = pack.splitlines()
    assert len(pack) <= MAX_PACK_CHARS
    assert lines[-1] == PACK_END
    cut_lines = [line for line in lines if line.startswith("CUT: ")]
    assert len(cut_lines) == 1
    assert re.fullmatch(r"CUT: \d+ characters removed", cut_lines[0])
    assert not any(line.startswith("T001: ") for line in lines)
    assert any(line.startswith("T040: ") for line in lines)
    assert all(row in lines for row in render_task_table(digest).splitlines()[2:])
    assert all(line in lines for line in render_criteria(digest.acceptance_criteria).splitlines())
    assert all(line in lines for line in known_unmet(digest))


def test_collect_repo_facts_reads_committed_modified_and_untracked(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "worker@example.test")
    _git(tmp_path, "config", "user.name", "Test Worker")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "a.txt").write_text("base\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("base\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    (tmp_path / "c.txt").write_text("committed\n", encoding="utf-8")
    _git(tmp_path, "add", "c.txt")
    _git(tmp_path, "commit", "-q", "-m", "add c")
    (tmp_path / "b.txt").write_text("modified\n", encoding="utf-8")
    (tmp_path / "d.txt").write_text("untracked\n", encoding="utf-8")

    facts = collect_repo_facts(str(tmp_path), "HEAD~1")

    assert "c.txt" in str(facts["diff_stat_committed"])
    assert "b.txt" not in str(facts["diff_stat_committed"])
    assert "b.txt" in str(facts["diff_stat_uncommitted"])
    assert "c.txt" not in str(facts["diff_stat_uncommitted"])
    assert facts["untracked_files"] == ["d.txt"]


def _git(workdir: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=workdir, check=True, capture_output=True)


def _make_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "worker@example.test")
    _git(repo, "config", "user.name", "Test Worker")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    (repo / "b.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    (repo / "c.txt").write_text("committed\n", encoding="utf-8")
    _git(repo, "add", "c.txt")
    _git(repo, "commit", "-q", "-m", "add c")
    (repo / "b.txt").write_text("modified\n", encoding="utf-8")
    (repo / "d.txt").write_text("untracked\n", encoding="utf-8")


def test_report_facts_route_returns_pack_for_a_git_repo(
    client: TestClient, workspace: Path
) -> None:
    repo = workspace / "repo"
    _make_repo(repo)
    response = client.post(
        "/v1/report-facts",
        json={"digest": _mission_digest(), "workdir": str(repo), "since_ref": "HEAD~1"},
    )
    assert response.status_code == 200
    markdown = response.json()["markdown"]
    assert PACK_HEADER in markdown
    assert "| T017 | Mission report |" in markdown
    for name in ("c.txt", "b.txt", "d.txt"):
        assert name in markdown


def test_report_facts_route_refuses_an_invalid_workdir(
    client: TestClient, workspace: Path
) -> None:
    response = client.post(
        "/v1/report-facts",
        json={
            "digest": _mission_digest(),
            "workdir": str(workspace / "missing"),
            "since_ref": "HEAD~1",
        },
    )
    assert response.status_code == 400


def test_report_facts_route_redacts_a_made_up_secret(client: TestClient, workspace: Path) -> None:
    repo = workspace / "repo"
    _make_repo(repo)
    digest = _raw_digest(
        [_raw_task("T001", "not_accepted", last_review_text="Config has PASSWORD=hunter2-example")]
    )
    response = client.post(
        "/v1/report-facts",
        json={"digest": digest, "workdir": str(repo), "since_ref": "HEAD~1"},
    )
    assert response.status_code == 200
    markdown = response.json()["markdown"]
    assert "hunter2-example" not in markdown
    assert REDACTED_TEXT in markdown
