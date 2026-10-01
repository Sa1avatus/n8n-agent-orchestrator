"""Retrieval profiles.

A profile is the complete, hashable retrieval configuration for one consumer role. ``worker`` and
``reviewer`` are independent: they differ in query construction, source priors, ranking features
and context budget. Requests may override any field through ``options`` (used by the Eval Harness
to compare configurations on identical tasks); the effective configuration is hashed into
``config_id`` which participates in cache keys and retrieval logs.

Ranking weights are hand-set, deterministic and documented. They are *not* learned: Learning-to-
Rank is deferred until enough relevance judgments exist (roadmap phase 6).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .text import short_hash

FreshnessMode = Literal["trust", "verify", "sync"]
SourceTypeName = Literal["code", "doc", "history"]


def _default_source_types() -> list[SourceTypeName]:
    return ["code", "doc"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RetrieverConfig(_Strict):
    lexical: bool = True
    vector: bool = True
    symbol: bool = True
    lexical_k: int = Field(50, ge=1, le=500)
    vector_k: int = Field(50, ge=1, le=500)
    symbol_k: int = Field(20, ge=1, le=200)
    rrf_k: int = Field(60, ge=1, le=1000)
    weights: dict[str, float] = Field(
        default_factory=lambda: {"lexical": 1.0, "vector": 1.0, "symbol": 1.2, "fresh": 1.0}
    )


class RerankConfig(_Strict):
    enabled: bool = True
    top_n: int = Field(40, ge=1, le=200)
    include_header: bool = False


class RankingConfig(_Strict):
    """Deterministic ranking layer applied after the text-only cross-encoder."""

    rerank: float = 0.55
    fused: float = 0.25
    exact_symbol: float = 0.10
    path_mentioned: float = 0.08
    scope_match: float = 0.06
    test_file: float = 0.0
    source_prior: dict[str, float] = Field(
        default_factory=lambda: {"code": 0.02, "doc": 0.0, "history": 0.0}
    )
    history_multiplier: float = Field(0.5, ge=0, le=1)
    # Demotion for fragments of .patch/.diff files longer than the threshold
    # (chunking.LARGE_PATCH_LINES, env RETRIEVAL_LARGE_PATCH_LINES) when the query does
    # not name the file; 0.8 halves such fragments' final score relative to equal evidence.
    large_patch_penalty: float = Field(0.8, ge=0, le=1)
    # Bonus for fragments whose file the Worker changed (request ``changed_paths``);
    # applied on top of the other features when the field is provided. Absent field:
    # the feature is 0 for every candidate, so behaviour is identical to today.
    # Must be large enough to lift a changed fragment across the min_final_score gate
    # and outrank other high-scoring fragments that would otherwise consume the budget.
    changed_path_bonus: float = Field(0.0, ge=0, le=2)


class BudgetConfig(_Strict):
    max_chunks: int = Field(12, ge=1, le=100)
    max_tokens: int = Field(6000, ge=100, le=100_000)
    # A task usually centres on one large file (e.g. an API module with many endpoints and
    # dependencies); 3 chunks per file dropped needed ones in favour of weaker files.
    per_path_limit: int = Field(8, ge=1, le=50)
    # Chunks below this final score were rarely used by the models and cost ~1/4 of the context;
    # 0.5 keeps ContextRecall within ~0.02 for both hashing and e5 (eval/results/2026-09-28-*).
    min_final_score: float = 0.5
    history_max_share: float = Field(0.2, ge=0, le=1)
    overlap_threshold: float = Field(0.5, ge=0, le=1)


class RenderConfig(_Strict):
    # Prefix each line of a code chunk with its file line number ("594| ..."), so the model can
    # cite file:line from the context instead of re-reading the file. Costs ~6 chars per line,
    # which counts against the token budget.
    line_numbers: bool = True


class CacheConfig(_Strict):
    enabled: bool = True
    semantic_reuse: bool = True
    jaccard_threshold: float = Field(0.75, ge=0, le=1)
    max_delta_chunks: int = Field(300, ge=0)


class Profile(_Strict):
    name: str
    description: str = ""
    source_types: list[SourceTypeName] = Field(default_factory=_default_source_types)
    freshness_mode: FreshnessMode = "sync"
    # auto: rag-platform when configured and reachable, otherwise the local index.
    backend: Literal["auto", "local", "rag_platform"] = "auto"
    pool_size: int = Field(60, ge=1, le=500)
    retrievers: RetrieverConfig = Field(default_factory=RetrieverConfig)
    rerank: RerankConfig = Field(default_factory=RerankConfig)
    ranking: RankingConfig = Field(default_factory=RankingConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    exclude_globs: list[str] = Field(default_factory=list)

    def config_id(self) -> str:
        payload = self.model_dump(mode="json", exclude={"description"})
        return "cfg_" + short_hash(json.dumps(payload, sort_keys=True), length=12)

    def with_overrides(self, overrides: dict[str, Any] | None) -> Profile:
        if not overrides:
            return self
        merged = _deep_merge(self.model_dump(mode="json"), overrides)
        return Profile.model_validate(merged)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def default_profiles() -> dict[str, Profile]:
    worker = Profile(
        name="worker",
        description=(
            "Context for solving the current task: code that must change or be understood, "
            "plus the documentation that constrains it."
        ),
    )
    reviewer = Profile(
        name="reviewer",
        description=(
            "Context for checking a Worker result: code the Worker touched, tests and validation "
            "entry points, and documentation behind the acceptance criteria."
        ),
        ranking=RankingConfig(
            rerank=0.5,
            fused=0.2,
            exact_symbol=0.08,
            path_mentioned=0.12,
            scope_match=0.05,
            test_file=0.06,
            source_prior={"code": 0.02, "doc": 0.02, "history": 0.0},
            # The Worker's own changed files are the Reviewer's primary focus; the bonus
            # must be large enough to lift a changed fragment across the min_final_score
            # gate even when its fused relevance is low (e.g. a file not named by the review),
            # and to outrank other high-scoring fragments that would otherwise consume the
            # budget and displace it.
            changed_path_bonus=1.5,
        ),
        budget=BudgetConfig(max_chunks=10, max_tokens=5000, per_path_limit=8),
    )
    return {"worker": worker, "reviewer": reviewer}


def load_profiles(path: str | None) -> dict[str, Profile]:
    profiles = default_profiles()
    if not path:
        return profiles
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    for name, override in data.items():
        base = profiles.get(name, Profile(name=name))
        profiles[name] = base.with_overrides({**override, "name": name})
    return profiles
