"""Bounded, case-insensitive search over the per-run event logs.

``search`` reads the ``<events_dir>/<run_id>.jsonl`` files line by line (a whole file is
never loaded into memory), the newest runs first. It matches the query as a plain
case-insensitive substring (``casefold``; no regex, no templates) in assistant text,
thinking, tool call names and arguments, tool results and the prompt, and accepts the same
filters as the dashboard (role, model, provider, cwd, status, session_id; several values of
one field are matched with OR, fields combine with AND) using the run metadata from the
store. The limits (the most runs scanned, per-run matches, total results, the time budget
and the snippet length) are module constants. When a limit stops the search early the
result's ``incomplete`` flag is set.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# Minimum query length after stripping (shorter queries return nothing without any file
# being read)
MIN_QUERY_LEN = 2
# At most this many matches per run
PER_RUN_MATCHES = 5
# The default number of results
DEFAULT_LIMIT = 50
# The most results a single search may return
MAX_LIMIT = 200
# The most runs a search scans: the result limit and the time budget end the search, the
# run list only bounds how many logs are read
MAX_RUNS_SCANNED = 2000
# The time budget of a search, in seconds
TIME_BUDGET_S = 3.0
# Snippet length around a match (characters on each side of it), so a large tool result is
# cut
SNIPPET_RADIUS = 160


def _snippet(text: str, start: int) -> str:
    """A window of SNIPPET_RADIUS characters around the match at ``start`` in ``text``."""
    lo = max(0, start - SNIPPET_RADIUS)
    hi = min(len(text), start + SNIPPET_RADIUS)
    prefix = "…" if lo > 0 else ""
    suffix = "…" if hi < len(text) else ""
    return prefix + text[lo:hi] + suffix


def _result(run: dict[str, Any], found: list[dict[str, Any]]) -> dict[str, Any]:
    """The per-run search result: the run's metadata and its matched snippets."""
    return {
        "run_id": run["run_id"],
        "session_id": run.get("session_id", ""),
        "role": run.get("role", ""),
        "model": run.get("model", ""),
        "started_at": run.get("started_at"),
        "matches": len(found),
        "snippets": found,
    }


def _fields(entry: dict[str, Any]) -> list[tuple[str, str]]:
    """The searchable text of one entry: (field name, text) pairs."""
    out: list[tuple[str, str]] = []
    kind = entry.get("kind")
    if kind in ("thinking", "text", "result"):
        out.append((kind, str(entry.get("text") or "")))
    elif kind in ("user", "init"):
        # the user text (the prompt) and the init entry are searched as the prompt
        out.append(("prompt", str(entry.get("text") or "")))
    elif kind == "tool_use":
        out.append(("name", str(entry.get("name") or "")))
        args = entry.get("input")
        if args:
            text = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
            out.append(("arguments", text))
    elif kind == "tool_result":
        out.append(("result", str(entry.get("text") or "")))
    return out


def _matches(query_cf: str, fields: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """The matching positions of the folded query in the entry's fields, with the field text."""
    out: list[dict[str, Any]] = []
    for field, text in fields:
        if not text:
            continue
        text_cf = text.casefold()
        start = text_cf.find(query_cf)
        while start != -1:
            out.append({"field": field, "start": start, "end": start + len(query_cf), "text": text})
            # skip the rest of this occurrence (no overlapping hits in the same field)
            start = text_cf.find(query_cf, start + len(query_cf))
    return out


def _list_run_ids(events_dir: Path, store: Any | None, max_runs: int) -> list[str]:
    """Run ids, newest first: from the store, falling back to a directory listing."""
    if store is not None:
        try:
            rows = store.list_runs(max_runs)
            return [run["run_id"] for run in rows]
        except Exception:  # the store is not usable: fall back to the directory
            pass
    try:
        files = sorted(
            (path for path in events_dir.glob("*.jsonl") if path.name != "crash.journal"),
            key=lambda path: (path.stat().st_mtime, path.name),
            reverse=True,
        )
    except OSError:
        return []
    return [path.name[: -len(".jsonl")] for path in files[:max_runs]]


def _match_run(
    path: Path,
    query_cf: str,
    limit: int,
    deadline: float,
    run: dict[str, Any],
    total: int,
) -> tuple[dict[str, Any] | None, int, bool]:
    """The matches of one run's log (up to PER_RUN_MATCHES), the total so far, and whether
    the time budget ran out inside the run."""
    if limit <= total:
        return None, total, False
    found: list[dict[str, Any]] = []
    timed_out = False
    try:
        with path.open(encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                if time.monotonic() > deadline:
                    timed_out = True  # the time budget ran out; keep the matches found so far
                    break
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a line cut by a crash
                for m in _matches(query_cf, _fields(entry)):
                    found.append(
                        {
                            "field": m["field"],
                            "start": m["start"],
                            "end": m["end"],
                            "snippet": _snippet(m["text"], m["start"]),
                            "seq": int(entry.get("seq") or 0),
                            "index": index,
                            "kind": entry.get("kind", ""),
                        }
                    )
                    if len(found) >= PER_RUN_MATCHES:
                        return _result(run, found), total + len(found), False
    except OSError:
        return None, total, False  # the journal file is missing
    if found:
        return _result(run, found), total + len(found), timed_out
    return None, total, timed_out


def _values(raw: Any) -> set[str]:
    """The active filter values: a string or a list (or other iterable) of strings."""
    if raw is None:
        return set()
    if isinstance(raw, str):
        return {raw} if raw else set()
    try:
        return {str(v) for v in raw if str(v)}
    except TypeError:
        return {str(raw)} if str(raw) else set()


def search(
    events_dir: Path,
    query: str,
    filters: Mapping[str, Any] | None = None,
    *,
    store: Any | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Search the run logs for ``query``; newest runs first, bounded by the module limits.

    ``filters`` holds the run-metadata fields (role, model, provider, cwd, status,
    session_id); several values of one field match with OR, the fields combine with AND
    (as in the dashboard). ``store`` (a ``Store``) supplies the run ids and the metadata used
    for the filters; without it the ids come from a directory listing. The query is never
    joined into a path. Returns ``{"runs": [...], "incomplete": bool}``.
    """
    query_cf = query.strip().casefold()
    if len(query_cf) < MIN_QUERY_LEN:
        return {"runs": [], "incomplete": False}
    limit = min(limit, MAX_LIMIT)
    if limit <= 0:
        return {"runs": [], "incomplete": False}
    if not events_dir.is_dir():
        return {"runs": [], "incomplete": False}
    run_ids = _list_run_ids(events_dir, store, MAX_RUNS_SCANNED)
    if not run_ids:
        return {"runs": [], "incomplete": False}
    values: dict[str, set[str]] = {}
    for field in ("role", "model", "provider", "cwd", "status", "session_id"):
        values[field] = _values((filters or {}).get(field))
    runs: list[dict[str, Any]] = []
    total = 0
    incomplete = False
    deadline = time.monotonic() + TIME_BUDGET_S
    for run_id in run_ids:
        run = (store.get_run(run_id) if store is not None else None) or {"run_id": run_id}
        active = {f: v for f, v in values.items() if v}
        if any(str(run.get(f) or "") not in v for f, v in active.items()):
            continue
        result, total, timed_out = _match_run(
            events_dir / f"{run_id}.jsonl", query_cf, limit, deadline, run, total
        )
        if timed_out:
            incomplete = True
        if result:
            runs.append(result)
            if len(runs) >= limit:
                return {"runs": runs, "incomplete": True}
    incomplete = incomplete or time.monotonic() > deadline
    return {"runs": runs, "incomplete": incomplete}
