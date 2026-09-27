"""Deterministic ranking layer.

Combines the text-only cross-encoder relevance with fused retrieval evidence and explicit,
explainable metadata / lexical / exact-match features. Every feature value is logged per
candidate so that Learning-to-Rank can later be trained on real relevance judgments; until then
the weights are fixed per profile (profiles.py).
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Sequence
from pathlib import PurePosixPath

from .candidates import Candidate
from .profiles import Profile
from .query_builder import BuiltQuery
from .text import extract_paths

TEST_PATH_RE = re.compile(
    r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]+\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$",
    re.IGNORECASE,
)

AUTHORITY = {
    "code": "current_code",
    "doc": "current_documentation",
    "history": "historical_evidence",
}


def _scope_patterns(scope: Sequence[str]) -> list[str]:
    patterns: list[str] = []
    for entry in scope:
        found = extract_paths(entry)
        if found:
            patterns.extend(found)
        elif entry.strip() and " " not in entry.strip():
            patterns.append(entry.strip())
    return [p.strip().lstrip("./") for p in patterns if p.strip()]


def _path_matches(path: str, reference: str) -> bool:
    ref = reference.strip().lstrip("./").rstrip("/")
    if not ref:
        return False
    if path == ref or path.endswith("/" + ref):
        return True
    if "/" not in ref and PurePosixPath(path).name == ref:
        return True
    return path.startswith(ref + "/") or fnmatch.fnmatch(path, ref)


def compute_features(candidate: Candidate, query: BuiltQuery, profile: Profile) -> None:
    record = candidate.record
    assert record is not None
    identifiers = {i.split(".")[-1].lower() for i in query.identifiers}
    qualified = {i.lower() for i in query.identifiers if "." in i}
    symbol = (record.symbol or "").split("#")[0]
    exact = float(
        bool(symbol)
        and (symbol.lower() in qualified or symbol.split(".")[-1].lower() in identifiers)
    )
    candidate.features = {
        "exact_symbol": exact,
        "path_mentioned": float(
            any(_path_matches(record.path, p) for p in query.paths if "/" in p or "." in p)
        ),
        "scope_match": float(
            any(_path_matches(record.path, p) for p in _scope_patterns(query.scope))
        ),
        "test_file": float(bool(TEST_PATH_RE.search(record.path))),
        "source_prior": profile.ranking.source_prior.get(record.source_type, 0.0),
        "fused": round(candidate.fused_norm, 6),
    }
    if candidate.reranker is not None:
        candidate.features["reranker"] = round(candidate.reranker, 6)


def rank_candidates(
    candidates: list[Candidate], query: BuiltQuery, profile: Profile, reranked: bool
) -> list[Candidate]:
    weights = profile.ranking
    for candidate in candidates:
        compute_features(candidate, query, profile)
        f = candidate.features
        candidate.deterministic = (
            weights.exact_symbol * f["exact_symbol"]
            + weights.path_mentioned * f["path_mentioned"]
            + weights.scope_match * f["scope_match"]
            + weights.test_file * f["test_file"]
            + f["source_prior"]
        )
        if not reranked:
            # Reranker disabled or unavailable: fused evidence takes the reranker's weight.
            relevance = (weights.rerank + weights.fused) * candidate.fused_norm
        elif candidate.reranker is not None:
            relevance = weights.rerank * candidate.reranker + weights.fused * candidate.fused_norm
        else:
            # Outside the reranked window: no cross-encoder evidence, so no reranker credit.
            relevance = weights.fused * candidate.fused_norm
        candidate.final = relevance + candidate.deterministic
        if candidate.record is not None and candidate.record.source_type == "history":
            candidate.final *= weights.history_multiplier
    ordered = sorted(candidates, key=lambda c: (-c.final, c.fused_rank, c.chunk_id))
    for rank, candidate in enumerate(ordered, start=1):
        candidate.final_rank = rank
    return ordered
