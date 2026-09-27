"""AHAWR-level outcome metrics from exported n8n Data Tables, joined with retrieval logs.

Input is an export of the ``Autonomous Agent Task Attempts`` Data Table (JSON array or CSV). The
harness only *reads* exports: it never writes AHAWR state and never uses retrieval data to
reconstruct it. Reviewer ``pass`` is counted as observed acceptance, not proven correctness.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from ..logstore import RetrievalLog
from .metrics import percentile

TS_FIELDS = ("updatedAt", "createdAt", "updated_at", "created_at", "ts")


def load_rows(path: str | Path) -> list[dict[str, Any]]:
    file = Path(path)
    text = file.read_text(encoding="utf-8-sig")
    if file.suffix.lower() == ".csv":
        sample = text.splitlines()[0] if text else ""
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        return list(csv.DictReader(text.splitlines(), delimiter=delimiter))
    data = json.loads(text)
    if isinstance(data, dict):
        data = data.get("data") or data.get("rows") or []
    return [row.get("json", row) if isinstance(row, dict) else {} for row in data]


def review_status(output: Any) -> str | None:
    """Extract ``pass``/``needs_changes`` like AHAWR's Parse Review node (first JSON object)."""
    text = str(output or "")
    depth, start, in_string, escaped = 0, -1, False, False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    candidate = json.loads(text[start : index + 1])
                except ValueError:
                    candidate = None
                if isinstance(candidate, dict):
                    status = str(candidate.get("status", "")).lower()
                    if status in {"pass", "needs_changes"}:
                        return status
                start = -1
    return None


def _timestamp(row: dict[str, Any]) -> float | None:
    for field in TS_FIELDS:
        value = row.get(field)
        if not value:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        except ValueError:
            continue
    return None


def task_outcomes(
    rows: list[dict[str, Any]], labels: dict[str, str] | None = None, default_label: str = "all"
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (str(row.get("state_key", "")), str(row.get("task_id", "")))
        if key[1]:
            grouped[key].append(row)
    outcomes = []
    for (state_key, task_id), items in sorted(grouped.items()):
        attempts = max(int(float(r.get("task_attempt") or 1)) for r in items)
        reviews: dict[int, str] = {}
        for row in items:
            if str(row.get("event_type", "")) != "reviewer_completed":
                continue
            status = review_status(row.get("reviewer_output"))
            if status:
                reviews[int(float(row.get("task_attempt") or 1))] = status
        stamps = [t for t in (_timestamp(r) for r in items) if t is not None]
        label = (
            (labels or {}).get(state_key)
            or str(items[0].get("retrieval_label") or "")
            or default_label
        )
        outcomes.append(
            {
                "state_key": state_key,
                "task_id": task_id,
                "label": label,
                "attempts": attempts,
                "first_pass": reviews.get(1) == "pass",
                "final_success": "pass" in reviews.values(),
                "latency_s": (max(stamps) - min(stamps)) if len(stamps) >= 2 else None,
            }
        )
    return outcomes


def retrieval_usage(log_path: str | Path) -> dict[tuple[str, str], dict[str, Any]]:
    """Per (state_namespace or mission_id, task_id): calls, cache hits, latency, tokens."""
    log = RetrievalLog(log_path)
    usage: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"calls": 0, "cache_hits": 0, "latency_ms": [], "reranker_ms": [], "tokens": []}
    )
    try:
        for request in log.requests():
            trace = json.loads(request["trace_json"] or "{}")
            scope = trace.get("state_namespace") or trace.get("mission_id")
            task = trace.get("task_id")
            if not scope or not task:
                continue
            entry = usage[(str(scope), str(task))]
            entry["calls"] += 1
            entry["cache_hits"] += request["cache_status"] in {
                "hit",
                "semantic_hit",
                "revalidated_hit",
            }
            timings = json.loads(request["timings_json"] or "{}")
            entry["latency_ms"].append(float(timings.get("total", 0.0)))
            if "rerank" in timings:
                entry["reranker_ms"].append(float(timings["rerank"]))
            entry["tokens"].append(int(request["context_tokens"]))
    finally:
        log.close()
    return dict(usage)


def aggregate(
    outcomes: list[dict[str, Any]],
    usage: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    by_label: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for outcome in outcomes:
        by_label[outcome["label"]].append(outcome)
    report: dict[str, dict[str, Any]] = {}
    for label, items in sorted(by_label.items()):
        n = len(items)
        latencies = [i["latency_s"] for i in items if i["latency_s"] is not None]
        entry: dict[str, Any] = {
            "tasks": n,
            "first_pass_rate": sum(i["first_pass"] for i in items) / n,
            "retry_rate": sum(i["attempts"] > 1 for i in items) / n,
            "avg_retries_per_task": sum(i["attempts"] - 1 for i in items) / n,
            "final_task_success_rate": sum(i["final_success"] for i in items) / n,
            "total_task_latency_s_mean": sum(latencies) / len(latencies) if latencies else None,
            "total_task_latency_s_p95": percentile(latencies, 0.95) if latencies else None,
        }
        if usage is not None:
            used = [
                usage[(i["state_key"], i["task_id"])]
                for i in items
                if (i["state_key"], i["task_id"]) in usage
            ]
            calls = sum(u["calls"] for u in used)
            latency = [v for u in used for v in u["latency_ms"]]
            rerank = [v for u in used for v in u["reranker_ms"]]
            tokens = [v for u in used for v in u["tokens"]]
            entry.update(
                {
                    "retrieval_calls_per_task": calls / n,
                    "cache_hit_ratio": sum(u["cache_hits"] for u in used) / calls
                    if calls
                    else None,
                    "retrieval_latency_ms_mean": sum(latency) / len(latency) if latency else None,
                    "reranker_latency_ms_mean": sum(rerank) / len(rerank) if rerank else None,
                    "context_tokens_mean": sum(tokens) / len(tokens) if tokens else None,
                }
            )
        report[label] = entry
    return report
