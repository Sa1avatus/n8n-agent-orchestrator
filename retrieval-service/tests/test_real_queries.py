"""Tests for the real-query dataset builder (eval/real_queries.py).

Fixtures are a temporary sqlite retrieval log (built via RetrievalLog), a saved runs
response and runner journals written as JSONL files, all under a real repository root
with real files. No network access.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from ahawr_retrieval.eval.real_queries import (
    _repo_path,
    balanced_sample,
    build_records,
    main,
    write_records,
)
from ahawr_retrieval.logstore import RetrievalLog

ROOT = "/d/repo"
FILE_BODY = "SECRET FILE BODY 0xDEADBEEF"


def _request(
    request_id: str, ts: float, profile: str, task: str, query: str
) -> dict[str, Any]:
    return {
        "request_id": request_id,
        "ts": ts,
        "profile": profile,
        "config_id": "c1",
        "corpora": ["ws"],
        "query_component": "task",
        "query_text": query,
        "query_hash": f"h-{request_id}",
        "cache_status": "miss",
        "degraded": False,
        "trace": {"mission_id": "m1", "task_id": task},
    }


def _wrap(title: str) -> str:
    return json.dumps({"components": {"task": title}})


def _journal_line(entry: dict[str, Any]) -> str:
    return json.dumps(entry)


def _read(path: str) -> dict[str, Any]:
    return {"kind": "tool_use", "name": "Read", "input": {"file_path": path}}


@pytest.fixture
def fixture_files(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "root"
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir(parents=True)
    (root / "app" / "parser.py").write_text("x\n", encoding="utf-8")
    (root / "tests" / "test_parser.py").write_text("x\n", encoding="utf-8")

    log_path = tmp_path / "logs.sqlite"
    log = RetrievalLog(log_path)
    for request in [
        _request(
            "r_worker",
            100.0,
            "worker",
            "T1",
            _wrap("Fix the parser function\nsecond line of the title"),
        ),
        _request(
            "r_reviewer",
            300.0,
            "reviewer",
            "T1",
            _wrap("Review the parser change in app/parser.py"),
        ),
        _request("r_secret", 500.0, "worker", "T2", "use api_key=abcdef123456 here"),
        _request("r_token", 600.0, "worker", "T4", "read the token line"),
        _request("r_nofiles", 700.0, "worker", "T3", "no file reads here"),
        _request("r_missing", 750.0, "worker", "T5", "open a missing file"),
        _request("r_glob", 800.0, "worker", "T6", "list the app directory"),
        _request(
            "r_notask",
            850.0,
            "worker",
            "T7",
            json.dumps({"components": {"query": "где обрабатываются ошибки"}}),
        ),
        _request("r_other", 900.0, "pipeline", "T1", _wrap("ignored profile")),
    ]:
        log.write(request, [])
    log.close()

    root_s = str(root)
    runs = [
        {"run_id": "run_w1", "role": "worker", "title": "TASK T1: fix", "started_at": 150.0},
        {"run_id": "run_rv", "role": "reviewer", "title": "Review: parser", "started_at": 350.0},
        {"run_id": "run_s2", "role": "worker", "title": "TASK T2: secret", "started_at": 550.0},
        {"run_id": "run_t4", "role": "worker", "title": "TASK T4: token", "started_at": 650.0},
        {"run_id": "run_n3", "role": "worker", "title": "TASK T3: none", "started_at": 750.0},
        {"run_id": "run_m5", "role": "worker", "title": "TASK T5: missing", "started_at": 800.0},
        {"run_id": "run_g6", "role": "worker", "title": "TASK T6: glob", "started_at": 850.0},
    ]
    runs_path = tmp_path / "runs.json"
    runs_path.write_text(json.dumps({"runs": runs}), encoding="utf-8")

    journals = tmp_path / "events"
    journals.mkdir()
    for run_id, events in [
        (
            "run_w1",
            [
                {"kind": "prompt", "text": "fix the parser"},
                _read(f"{root_s}/app/parser.py"),
                {"kind": "tool_use", "name": "Bash", "input": {"command": "cat app/parser.py"}},
                {
                    "kind": "tool_use",
                    "name": "Edit",
                    "input": {"file_path": f"{root_s}/app/parser.py"},
                },
                {"kind": "tool_result", "text": FILE_BODY},
            ],
        ),
        ("run_rv", [_read(f"{root_s}/tests/test_parser.py")]),
        ("run_s2", [_read(f"{root_s}/app/parser.py")]),
        ("run_t4", [_read(f"{root_s}/app/parser.py")]),
        ("run_n3", [{"kind": "tool_use", "name": "Bash", "input": {"command": "echo hi"}}]),
        ("run_m5", [_read(f"{root_s}/missing.py"), _read("/elsewhere/other.py")]),
        ("run_g6", [{"kind": "tool_use", "name": "Bash", "input": {"command": "cat app/*.py"}}]),
    ]:
        lines = [_journal_line(event) for event in events]
        (journals / f"{run_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    return {"log": log_path, "runs": runs_path, "journals": journals, "root": root}


def _load_runs(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["runs"]


def _records(fixture_files: dict[str, Path]) -> list[dict[str, Any]]:
    return build_records(
        fixture_files["log"],
        _load_runs(fixture_files["runs"]),
        fixture_files["journals"],
        str(fixture_files["root"]),
    )


def test_joins_runs_to_queries_and_drops_rows_without_paths(fixture_files: dict[str, Path]) -> None:
    records = _records(fixture_files)

    assert {r["id"]: r["expected_paths"] for r in records} == {
        "r_worker": ["app/parser.py"],
        "r_reviewer": ["tests/test_parser.py"],
    }
    by_id = {r["id"]: r for r in records}
    assert by_id["r_worker"]["role"] == "worker"
    assert by_id["r_worker"]["query_type"] == "words"
    assert by_id["r_reviewer"]["role"] == "reviewer"
    assert by_id["r_reviewer"]["query_type"] == "path"


def test_multi_line_titles_are_cut_to_the_first_line(fixture_files: dict[str, Path]) -> None:
    by_id = {r["id"]: r for r in _records(fixture_files)}
    assert by_id["r_worker"]["query"] == "Fix the parser function"
    for record in _records(fixture_files):
        assert "\n" not in record["query"]
        assert not record["query"].startswith("{")


def test_queries_without_a_task_title_are_dropped(fixture_files: dict[str, Path]) -> None:
    ids = {r["id"] for r in _records(fixture_files)}
    assert "r_notask" not in ids


def test_secret_and_literal_secret_words_are_dropped(fixture_files: dict[str, Path]) -> None:
    records = _records(fixture_files)
    ids = {r["id"] for r in records}
    assert "r_secret" not in ids
    # a bare "token" matches the literal scan, not the key=value patterns
    assert "r_token" not in ids
    assert "abcdef123456" not in json.dumps(records)


def test_only_real_files_under_the_root_are_kept(fixture_files: dict[str, Path]) -> None:
    ids = {r["id"] for r in _records(fixture_files)}
    # missing file, glob, and a path outside the root all drop their records
    assert "r_missing" not in ids
    assert "r_glob" not in ids


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (f"{ROOT}/app/parser.py", "app/parser.py"),
        ("app/parser.py", "app/parser.py"),
        ("/elsewhere/other.py", ""),
        (f"{ROOT}repository/x.py", ""),
        (f"{ROOT}/../x.py", ""),
    ],
)
def test_paths_are_made_repository_relative(path: str, expected: str) -> None:
    assert _repo_path(path, ROOT) == expected


def _record(record_id: str, role: str, query_type: str, query: str) -> dict[str, Any]:
    return {
        "id": record_id,
        "query": query,
        "role": role,
        "expected_paths": ["a.py"],
        "query_type": query_type,
    }


def test_balanced_sample_is_round_robin_by_role_and_query_type() -> None:
    rows = [
        ("w1", "worker", "code"),
        ("w2", "worker", "code"),
        ("w3", "worker", "path"),
        ("w4", "worker", "words"),
        ("w5", "worker", "words"),
        ("r1", "reviewer", "path"),
        ("r2", "reviewer", "code"),
        ("r3", "reviewer", "words"),
    ]
    records = [_record(rid, role, qtype, qtype) for rid, role, qtype in rows]

    sample = balanced_sample(records, 3)
    assert sorted((r["role"], r["query_type"]) for r in sample) == [
        ("reviewer", "code"),
        ("reviewer", "path"),
        ("reviewer", "words"),
        ("worker", "code"),
        ("worker", "path"),
        ("worker", "words"),
    ]
    full = balanced_sample(records, 10)
    assert len(full) == len(records)
    assert {r["id"] for r in full} == {r["id"] for r in records}


def test_records_hold_only_the_five_fields_and_no_file_content(
    fixture_files: dict[str, Path], tmp_path: Path
) -> None:
    records = _records(fixture_files)
    out = tmp_path / "real_queries.jsonl"
    write_records(records, out, fixture_files["log"], fixture_files["journals"])

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        assert set(json.loads(line)) == {"id", "query", "role", "expected_paths", "query_type"}
    assert FILE_BODY not in out.read_text(encoding="utf-8")


def test_live_log_and_journals_are_never_written(fixture_files: dict[str, Path]) -> None:
    log_bytes = fixture_files["log"].read_bytes()
    digest = hashlib.sha256(log_bytes).hexdigest()
    records = _records(fixture_files)
    assert hashlib.sha256(fixture_files["log"].read_bytes()).hexdigest() == digest

    with pytest.raises(ValueError, match="refusing to write"):
        write_records(
            records, fixture_files["log"], fixture_files["log"], fixture_files["journals"]
        )
    with pytest.raises(ValueError, match="refusing to write"):
        write_records(
            records,
            fixture_files["journals"] / "x.jsonl",
            fixture_files["log"],
            fixture_files["journals"],
        )


def test_cli_writes_the_dataset(fixture_files: dict[str, Path], tmp_path: Path) -> None:
    out = tmp_path / "queries.jsonl"
    code = main(
        [
            "--log",
            str(fixture_files["log"]),
            "--runs",
            str(fixture_files["runs"]),
            "--journals",
            str(fixture_files["journals"]),
            "--root",
            str(fixture_files["root"]),
            "--out",
            str(out),
        ]
    )

    assert code == 0
    assert [json.loads(line)["id"] for line in out.read_text(encoding="utf-8").splitlines()] == [
        "r_worker",
        "r_reviewer",
    ]

    sample_out = tmp_path / "sample.jsonl"
    code = main(
        [
            "--log",
            str(fixture_files["log"]),
            "--runs",
            str(fixture_files["runs"]),
            "--journals",
            str(fixture_files["journals"]),
            "--root",
            str(fixture_files["root"]),
            "--per-role",
            "1",
            "--out",
            str(sample_out),
        ]
    )
    assert code == 0
    sample_ids = sorted(
        json.loads(line)["id"] for line in sample_out.read_text(encoding="utf-8").splitlines()
    )
    assert sample_ids == ["r_reviewer", "r_worker"]


def test_malformed_journal_line_is_reported(fixture_files: dict[str, Path]) -> None:
    (fixture_files["journals"] / "run_w1.jsonl").write_text("{not json\n", encoding="utf-8")

    with pytest.raises(ValueError, match="run_w1.jsonl:1: not a JSON journal entry"):
        build_records(
            fixture_files["log"],
            _load_runs(fixture_files["runs"]),
            fixture_files["journals"],
            str(fixture_files["root"]),
        )
