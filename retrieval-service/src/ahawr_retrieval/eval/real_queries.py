"""Real-query dataset: Worker and Reviewer queries joined to the files their run used.

The builder reads a copy of the retrieval log (opened read-only) and the runner's per-run
journals (``<run_id>.jsonl``) plus the saved ``GET /v1/runs`` response, and writes one JSONL
record per query: ``{id, query, role, expected_paths, query_type}``. Records hold paths
and the query text only, never file contents. The query is the ``components.task``
title of the stored query wrapper, taken as a single line cut to
:data:`_MAX_QUERY_CHARS`. ``expected_paths`` are the files the matched run actually
opened (Read/Edit/Write or Bash file operands) that exist under ``--root``. Records are
dropped when the query is empty, multi-line or JSON-shaped, or when any field matches a
secret pattern; the sample is balanced by role and query type.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..usage import (
    _match_run_to_request,
    enrich_runs,
    normalize_path,
    opened_files_from_events,
    query_type,
    request_scope,
)

ROLES = ("worker", "reviewer")
QUERY_TYPES = ("code", "path", "words")
_MAX_QUERY_CHARS = 200
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*\S{6,}", re.IGNORECASE),
    re.compile(r"\bbearer\s+[A-Za-z0-9._~+/-]{16,}", re.IGNORECASE),
)
# The literal scan the dataset must pass: any of these words in any field drops the record.
_SCAN_RE = re.compile(r"token|password|secret|api[_-]?key|postgres://|chat_id", re.IGNORECASE)


def _read_requests(log_path: Path) -> list[dict[str, Any]]:
    uri = f"{log_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT request_id, ts, profile, trace_mission_id, trace_task_id, trace_json, "
            "query_text, query_component FROM retrieval_requests "
            "WHERE profile IN (?, ?) ORDER BY ts",
            ROLES,
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def _read_journal(journal_dir: Path, run_id: str) -> list[dict[str, Any]]:
    path = journal_dir / f"{run_id}.jsonl"
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{number}: not a JSON journal entry") from error
    return entries


def _repo_path(path: str, root: str) -> str:
    """A repository-relative path, or ``""`` for a path outside the repository."""
    if path.startswith("/") and not path.startswith(root.rstrip("/") + "/"):
        return ""
    relative = normalize_path(path, root)
    if not relative or ".." in Path(relative).parts:
        return ""
    return relative


def _short_query(query_text: str) -> str:
    """The short single-line search string of a stored query, or ``""``.

    Unwraps the JSON wrapper, takes the ``components.task`` title (or the first line of
    the text when the text is plain), and cuts to :data:`_MAX_QUERY_CHARS`. Empty text,
    a wrapper without a task title, and lines starting with ``{`` are rejected.
    """
    text = (query_text or "").strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return ""
        components = data.get("components")
        title = components.get("task") if isinstance(components, dict) else None
        if not (isinstance(title, str) and title.strip()):
            return ""
        text = title
    line = text.split("\n", 1)[0].strip()
    if not line or line.startswith("{"):
        return ""
    return line[:_MAX_QUERY_CHARS]


def _has_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)


def _expected_paths(
    requests: list[dict[str, Any]],
    runs: list[dict[str, Any]],
    journal_dir: Path,
    root: str,
) -> dict[str, set[str]]:
    request_key = {str(r["request_id"]): request_scope(r) for r in requests}
    request_ts = {str(r["request_id"]): float(r["ts"]) for r in requests}
    profile_of = {str(r["request_id"]): str(r["profile"]) for r in requests}
    expected: dict[str, set[str]] = {}
    for run in enrich_runs(runs, requests):
        request_id = _match_run_to_request(run, requests, request_key, request_ts, profile_of)
        if request_id is None:
            continue
        opened, _ = opened_files_from_events(_read_journal(journal_dir, str(run["run_id"])))
        for path in opened:
            relative = _repo_path(path, root)
            if not relative:
                continue
            # keep only real files: no globs, directories or names left over from commands
            if not os.path.isfile(os.path.join(root, relative)):
                continue
            expected.setdefault(request_id, set()).add(relative)
    return expected


def build_records(
    log_path: Path, runs: list[dict[str, Any]], journal_dir: Path, root: str
) -> list[dict[str, Any]]:
    requests = _read_requests(log_path)
    expected = _expected_paths(requests, runs, journal_dir, root)
    records: list[dict[str, Any]] = []
    for request in requests:
        query = _short_query(request["query_text"])
        if not query or _has_secret(query):
            continue
        paths = sorted(expected.get(request["request_id"], ()))
        if not paths:
            continue
        record = {
            "id": request["request_id"],
            "query": query,
            "role": request["profile"],
            "expected_paths": paths,
            "query_type": query_type(query),
        }
        # the literal scan covers every field, not only the query
        if _SCAN_RE.search(json.dumps(record)):
            continue
        records.append(record)
    return records


def balanced_sample(records: list[dict[str, Any]], per_role: int) -> list[dict[str, Any]]:
    """A deterministic sample of at most ``per_role`` records per role, round-robin
    across query types (each type's records in ts order), so the sample is balanced by
    role and by query type as far as the data allows."""
    pools: dict[str, dict[str, list[dict[str, Any]]]] = {
        role: {query_type: [] for query_type in QUERY_TYPES} for role in ROLES
    }
    for record in records:
        pools[record["role"]].setdefault(record["query_type"], []).append(record)
    selected: list[dict[str, Any]] = []
    for role in ROLES:
        taken = 0
        while taken < per_role:
            progressed = False
            for query_type in QUERY_TYPES:
                pool = pools[role][query_type]
                if pool and taken < per_role:
                    selected.append(pool.pop(0))
                    taken += 1
                    progressed = True
            if not progressed:
                break
    return selected


def write_records(
    records: Iterable[dict[str, Any]], out: Path, log_path: Path, journal_dir: Path
) -> None:
    resolved = out.resolve()
    if resolved == log_path.resolve() or journal_dir.resolve() in resolved.parents:
        raise ValueError(f"refusing to write into a live file or journal folder: {out}")
    with resolved.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the real-query eval dataset (JSONL).")
    parser.add_argument("--log", type=Path, required=True, help="copy of the retrieval log sqlite")
    parser.add_argument("--runs", type=Path, required=True, help="saved GET /v1/runs response")
    parser.add_argument("--journals", type=Path, required=True, help="runner events folder")
    parser.add_argument("--root", required=True, help="repository root paths are made relative to")
    parser.add_argument("--out", type=Path, required=True, help="output JSONL file")
    parser.add_argument(
        "--per-role",
        type=int,
        default=0,
        help="take this many records per role (0 = write all qualified records)",
    )
    args = parser.parse_args(argv)
    runs = json.loads(args.runs.read_text(encoding="utf-8")).get("runs") or []
    records = build_records(args.log, runs, args.journals, args.root)
    if args.per_role > 0:
        records = balanced_sample(records, args.per_role)
    write_records(records, args.out, args.log, args.journals)
    print(f"{len(records)} records written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
