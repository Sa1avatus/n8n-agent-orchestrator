"""Baseline-vs-candidate comparison reports (Markdown + JSON)."""

from __future__ import annotations

from typing import Any

HEADLINE = (
    "Recall@5",
    "Recall@10",
    "Precision@5",
    "MRR",
    "nDCG@5",
    "nDCG@10",
    "ContextRecall",
    "file_Recall@5",
    "file_MRR",
)
SYSTEM = (
    "retrieval_latency_ms_mean",
    "retrieval_latency_ms_p95",
    "reranker_latency_ms_mean",
    "context_tokens_mean",
    "cache_hit_ratio",
    "cache_decision_accuracy",
    "retrieval_calls_per_task",
    "degraded_rate",
)
LOWER_IS_BETTER = {
    "retrieval_latency_ms_mean",
    "retrieval_latency_ms_p95",
    "reranker_latency_ms_mean",
    "context_tokens_mean",
    "retrieval_calls_per_task",
    "degraded_rate",
}


def compare(results: dict[str, dict[str, Any]], baseline: str) -> dict[str, Any]:
    if baseline not in results:
        raise ValueError(f"baseline {baseline!r} not among results {sorted(results)}")
    base = results[baseline]["aggregate"]
    comparison: dict[str, Any] = {"baseline": baseline, "configs": {}}
    for name, result in results.items():
        agg = result["aggregate"]
        retrieval = {
            metric: {
                "value": agg["retrieval"].get(metric),
                "delta": _delta(agg["retrieval"].get(metric), base["retrieval"].get(metric)),
            }
            for metric in sorted(agg["retrieval"])
        }
        system = {
            metric: {
                "value": agg["system"].get(metric),
                "delta": _delta(agg["system"].get(metric), base["system"].get(metric)),
            }
            for metric in SYSTEM
        }
        comparison["configs"][name] = {"retrieval": retrieval, "system": system}
    return comparison


def _delta(value: float | None, reference: float | None) -> float | None:
    if value is None or reference is None:
        return None
    return value - reference


def markdown(results: dict[str, dict[str, Any]], baseline: str) -> str:
    comparison = compare(results, baseline)
    names = list(results)
    first = results[names[0]]["dataset"]
    lines = [
        f"# Retrieval evaluation: {first['id']} v{first['version']} ({first['kind']}, "
        f"{first['tasks']} tasks)",
        "",
        f"Baseline: `{baseline}`. Deltas are relative to the baseline; ▲/▼ mark improvements "
        "and regressions (latency, tokens and calls are better when lower).",
        "",
        "## Retrieval quality",
        "",
        "| metric | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(names),
    ]
    for metric in HEADLINE:
        row = [metric]
        for name in names:
            cell = comparison["configs"][name]["retrieval"].get(metric)
            row.append(_cell(cell, metric, name == baseline))
        lines.append("| " + " | ".join(row) + " |")
    lines += [
        "",
        "## System",
        "",
        "| metric | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(names),
    ]
    for metric in SYSTEM:
        row = [metric]
        for name in names:
            row.append(
                _cell(comparison["configs"][name]["system"][metric], metric, name == baseline)
            )
        lines.append("| " + " | ".join(row) + " |")
    lines += [
        "",
        "## Per-task (MRR / Recall@5)",
        "",
        "| task | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(names),
    ]
    task_ids = [t["task_id"] for t in results[names[0]]["tasks"]]
    for task_id in task_ids:
        row = [task_id]
        for name in names:
            task = next(t for t in results[name]["tasks"] if t["task_id"] == task_id)
            row.append(f"{task['metrics']['MRR']:.2f} / {task['metrics']['Recall@5']:.2f}")
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def _cell(cell: dict[str, Any] | None, metric: str, is_baseline: bool) -> str:
    if not cell or cell["value"] is None:
        return "–"
    value = cell["value"]
    text = f"{value:.3f}" if abs(value) < 100 else f"{value:.0f}"
    delta = cell["delta"]
    if is_baseline or delta is None or abs(delta) < 1e-9:
        return text
    better = delta < 0 if metric in LOWER_IS_BETTER else delta > 0
    return f"{text} ({'▲' if better else '▼'}{delta:+.3f})"
