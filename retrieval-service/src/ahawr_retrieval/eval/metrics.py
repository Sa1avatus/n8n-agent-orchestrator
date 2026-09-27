"""Retrieval metrics over graded judgments.

Judgments identify relevant *units* (a file, a symbol in a file, a doc section, or an exact
chunk) rather than raw chunk ids, so gold labels survive chunking changes. A retrieved chunk
matches the first judgment it satisfies; each judgment counts once (later duplicates earn no
gain), which keeps nDCG and Recall honest when several chunks of one symbol are retrieved.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

DEFAULT_KS = (1, 3, 5, 10)
# Judgment symbol for module-level code outside any symbol (imports, module docstring, constants).
MODULE_SYMBOL = "<module>"


@dataclass(frozen=True)
class Judgment:
    path: str
    grade: int = 1
    symbol: str | None = None
    section: str | None = None
    chunk_id: str | None = None

    def key(self) -> str:
        return f"{self.path}::{self.symbol or ''}::{self.section or ''}::{self.chunk_id or ''}"

    def matches(self, chunk: dict[str, Any]) -> bool:
        if self.chunk_id:
            return bool(chunk.get("chunk_id") == self.chunk_id)
        if chunk.get("path") != self.path:
            return False
        if self.symbol == MODULE_SYMBOL:
            if chunk.get("symbol"):
                return False
        elif self.symbol:
            symbol = (chunk.get("symbol") or "").split("#")[0]
            # exact symbol, a member of it (Class -> Class.method), or an enclosing chunk that
            # holds the whole symbol (small classes are one chunk: Class.method -> Class)
            if not (
                symbol == self.symbol
                or symbol.startswith(self.symbol + ".")
                or (symbol and self.symbol.startswith(symbol + "."))
            ):
                return False
        if self.section:
            section = chunk.get("section") or ""
            if self.section.lower() not in section.lower():
                return False
        return True


def label_ranking(
    ranking: Sequence[dict[str, Any]], judgments: Sequence[Judgment], file_level: bool = False
) -> list[tuple[str | None, int]]:
    """Map a ranked chunk list to ``(judgment key or None, gain)``; judgments count once."""
    if file_level:
        judgments = _file_judgments(judgments)
    used: set[str] = set()
    labels: list[tuple[str | None, int]] = []
    for chunk in ranking:
        match = next((j for j in judgments if j.matches(chunk)), None)
        if match is None:
            labels.append((None, 0))
            continue
        key = match.key()
        if key in used:
            labels.append((key, 0))
        else:
            used.add(key)
            labels.append((key, match.grade))
    return labels


def _file_judgments(judgments: Sequence[Judgment]) -> list[Judgment]:
    best: dict[str, int] = {}
    for judgment in judgments:
        best[judgment.path] = max(best.get(judgment.path, 0), judgment.grade)
    return [Judgment(path=p, grade=g) for p, g in best.items()]


def ranking_metrics(
    ranking: Sequence[dict[str, Any]],
    judgments: Sequence[Judgment],
    ks: Iterable[int] = DEFAULT_KS,
    file_level: bool = False,
) -> dict[str, float]:
    effective = _file_judgments(judgments) if file_level else list(judgments)
    relevant = [j for j in effective if j.grade > 0]
    labels = label_ranking(ranking, effective)
    metrics: dict[str, float] = {}
    for k in ks:
        top = labels[:k]
        found = {key for key, gain in top if key is not None and gain > 0}
        metrics[f"Recall@{k}"] = len(found) / len(relevant) if relevant else 0.0
        metrics[f"Precision@{k}"] = sum(1 for key, _ in top if key is not None) / k
        metrics[f"nDCG@{k}"] = _ndcg([gain for _, gain in labels], [j.grade for j in relevant], k)
    first = next((i for i, (key, _) in enumerate(labels, 1) if key is not None), None)
    metrics["MRR"] = 1.0 / first if first else 0.0
    return metrics


def _ndcg(gains: list[int], ideal_grades: list[int], k: int) -> float:
    ideal = _dcg(sorted(ideal_grades, reverse=True)[:k])
    return _dcg(gains[:k]) / ideal if ideal else 0.0


def _dcg(grades: Iterable[int]) -> float:
    return float(sum((2**g - 1) / math.log2(i + 1) for i, g in enumerate(grades, 1)))


def mean(rows: Iterable[dict[str, float]]) -> dict[str, float]:
    items = list(rows)
    if not items:
        return {}
    names = sorted(set().union(*(r.keys() for r in items)))
    return {n: sum(r.get(n, 0.0) for r in items) / len(items) for n in names}


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))
    return float(ordered[index])
