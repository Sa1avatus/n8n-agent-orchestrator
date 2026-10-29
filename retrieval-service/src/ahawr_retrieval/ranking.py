"""Deterministic ranking layer.

Combines the text-only cross-encoder relevance with fused retrieval evidence and explicit,
explainable metadata / lexical / exact-match features. Every feature value is logged per
candidate so that Learning-to-Rank can later be trained on real relevance judgments; until then
the weights are fixed per profile (profiles.py).
"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Sequence
from pathlib import PurePosixPath

from .candidates import Candidate
from .chunking import LARGE_PATCH_LINES
from .profiles import Profile
from .query_builder import BuiltQuery
from .store import ChunkRecord
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


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# Ranking env flags — all OFF by default; behavior is byte-for-byte unchanged when off.
_RANKING_USE_RRF = _bool_env("RETRIEVAL_RANKING_USE_RRF", False)
_RANKING_RRF_K = int(os.environ.get("RETRIEVAL_RANKING_RRF_K", "") or "60")
_RANKING_MAX_FRAGMENTS_PER_FILE = int(
    os.environ.get("RETRIEVAL_RANKING_MAX_FRAGMENTS_PER_FILE", "") or "0"
)
_RANKING_LEXICAL_WEIGHT = float(
    os.environ.get("RETRIEVAL_RANKING_LEXICAL_WEIGHT", "") or "1.0"
)
_RANKING_VECTOR_WEIGHT = float(
    os.environ.get("RETRIEVAL_RANKING_VECTOR_WEIGHT", "") or "1.0"
)
_RANKING_MIN_FINAL_SCORE = float(
    os.environ.get("RETRIEVAL_RANKING_MIN_FINAL_SCORE", "") or "0.0"
)


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


def _is_large_patch(record: ChunkRecord, query: BuiltQuery, file_line_count: int | None) -> bool:
    """True when the fragment comes from a .patch/.diff file whose *whole file* is
    longer than the threshold and the query/task text does not name the file
    (path or basename). The check uses the whole-file line count, not the
    fragment span, so a single fragment of a long patch still counts as large."""
    if record.language != "diff":
        return False
    if file_line_count is None or file_line_count <= LARGE_PATCH_LINES:
        return False
    return not any(_path_matches(record.path, p) for p in query.paths)


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
        "changed_path": float(any(_path_matches(record.path, p) for p in query.changed_paths)),
        "scope_match": float(
            any(_path_matches(record.path, p) for p in _scope_patterns(query.scope))
        ),
        "test_file": float(bool(TEST_PATH_RE.search(record.path))),
        "source_prior": profile.ranking.source_prior.get(record.source_type, 0.0),
        "fused": round(candidate.fused_norm, 6),
        "large_patch": float(_is_large_patch(record, query, candidate.file_line_count)),
    }
    if candidate.reranker is not None:
        candidate.features["reranker"] = round(candidate.reranker, 6)


def rank_candidates(
    candidates: list[Candidate],
    query: BuiltQuery,
    profile: Profile,
    reranked: bool,
    file_line_counts: dict[tuple[str, str], int] | None = None,
) -> list[Candidate]:
    weights = profile.ranking
    for candidate in candidates:
        if file_line_counts and candidate.record is not None:
            candidate.file_line_count = file_line_counts.get(
                (candidate.record.corpus_id, candidate.record.path)
            )
        compute_features(candidate, query, profile)
        f = candidate.features
        candidate.deterministic = (
            weights.exact_symbol * f["exact_symbol"]
            + weights.path_mentioned * f["path_mentioned"]
            + weights.changed_path_bonus * f["changed_path"]
            + weights.scope_match * f["scope_match"]
            + weights.test_file * f["test_file"]
            + f["source_prior"]
        )
        if _RANKING_USE_RRF:
            # RRF fusion mode: weighted reciprocal ranks per retriever with configurable k;
            # a retriever the candidate was not ranked in contributes nothing.
            lexical_w = _RANKING_LEXICAL_WEIGHT
            vector_w = _RANKING_VECTOR_WEIGHT
            rrf_k_value = _RANKING_RRF_K
            relevance = 0.0
            if "lexical" in candidate.ranks:
                relevance += lexical_w / (rrf_k_value + candidate.ranks["lexical"])
            if "vector" in candidate.ranks:
                relevance += vector_w / (rrf_k_value + candidate.ranks["vector"])
        elif not reranked:
            # Reranker disabled or unavailable: fused evidence takes the reranker's weight.
            relevance = (weights.rerank + weights.fused) * candidate.fused_norm
        elif candidate.reranker is not None:
            relevance = weights.rerank * candidate.reranker + weights.fused * candidate.fused_norm
        else:
            # Outside the reranked window: no cross-encoder evidence, so no reranker credit.
            relevance = weights.fused * candidate.fused_norm
        candidate.final = relevance + candidate.deterministic
        if f["large_patch"]:
            candidate.final *= 1.0 - weights.large_patch_penalty
        if candidate.record is not None and candidate.record.source_type == "history":
            candidate.final *= weights.history_multiplier
    ordered = sorted(candidates, key=lambda c: (-c.final, c.fused_rank, c.chunk_id))
    # Optional minimum final score cutoff behind flag; off (0.0) leaves behavior unchanged.
    if _RANKING_MIN_FINAL_SCORE > 0:
        ordered = [c for c in ordered if c.final >= _RANKING_MIN_FINAL_SCORE]
    # Per-file fragment cap when set (> 0); when unset (0) no cap is applied.
    if _RANKING_MAX_FRAGMENTS_PER_FILE > 0:
        path_counts: dict[str, int] = {}
        capped_ordered: list[Candidate] = []
        for candidate in ordered:
            path = candidate.record.path if candidate.record is not None else candidate.chunk_id
            count = path_counts.get(path, 0)
            if count < _RANKING_MAX_FRAGMENTS_PER_FILE:
                capped_ordered.append(candidate)
                path_counts[path] = count + 1
        ordered = capped_ordered
    for rank, candidate in enumerate(ordered, start=1):
        candidate.final_rank = rank
    return ordered
