"""HTTP API contracts for /retrieve, /index and /invalidate."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CORPUS_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
SourceType = Literal["code", "doc", "history"]


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if isinstance(value, list | tuple):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


class TaskContext(BaseModel):
    """Task fields as produced by the AHAWR Architect plan. Only used to build queries."""

    model_config = ConfigDict(extra="ignore")
    mission_id: str | None = None
    task_id: str | None = None
    title: str = ""
    objective: str = ""
    instructions: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    verification: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    mission_objective: str = ""

    @field_validator("acceptance_criteria", "verification", "scope", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> list[str]:
        return _as_list(value)

    @field_validator("title", "objective", "instructions", "mission_objective", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return "" if value is None else str(value)


class ReviewContext(BaseModel):
    model_config = ConfigDict(extra="ignore")
    worker_output: str = ""
    feedback: str = ""
    changed_files: list[str] = Field(default_factory=list)

    @field_validator("changed_files", mode="before")
    @classmethod
    def _lists(cls, value: Any) -> list[str]:
        return _as_list(value)

    @field_validator("worker_output", "feedback", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return "" if value is None else str(value)


class WorkspaceState(BaseModel):
    """Caller's view of corpus state. ``strict`` drops chunks from any other snapshot."""

    code_snapshot: str | None = None
    docs_snapshot: str | None = None
    doc_versions: dict[str, str] = Field(default_factory=dict)
    strict: bool = False


class TraceContext(BaseModel):
    """Opaque correlation identifiers written to retrieval logs for offline analysis only.

    They never influence retrieval results and are never read back for recovery
    (ARCHITECTURE_CONTRACT.md §5.1).
    """

    model_config = ConfigDict(extra="allow")
    mission_id: str | None = None
    task_id: str | None = None
    attempt: int | None = None
    role: str | None = None
    correlation_id: str | None = None
    label: str | None = None


class BudgetOverride(BaseModel):
    max_chunks: int | None = Field(None, ge=1, le=100)
    max_tokens: int | None = Field(None, ge=100, le=100_000)


class RetrieveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: str = "worker"
    corpora: list[str] = Field(min_length=1, max_length=16)
    # Optional roots for corpora that do not exist yet (e.g. an AHAWR mission's working
    # directory): indexed on first use, within RETRIEVAL_ALLOWED_ROOTS, host paths mapped
    # through RETRIEVAL_PATH_MAP.
    corpus_roots: dict[str, str] = Field(default_factory=dict, max_length=16)
    task: TaskContext = Field(default_factory=TaskContext)
    review: ReviewContext | None = None
    query: str | None = Field(None, max_length=20_000)
    workspace_state: WorkspaceState | None = None
    freshness_mode: Literal["trust", "verify", "sync"] | None = None
    budget: BudgetOverride | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    cache: Literal["use", "refresh", "bypass"] = "use"
    trace: TraceContext | None = None
    include_candidates: bool = False
    render_context: bool = True

    @field_validator("corpora")
    @classmethod
    def _corpora(cls, value: list[str]) -> list[str]:
        import re

        for corpus in value:
            if not re.match(CORPUS_ID_PATTERN, corpus):
                raise ValueError(f"invalid corpus id: {corpus!r}")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def _has_query_material(self) -> RetrieveRequest:
        t = self.task
        material = [self.query or "", t.title, t.objective, t.instructions, *t.acceptance_criteria]
        if self.review:
            material += [self.review.feedback, self.review.worker_output]
        if not any(part.strip() for part in material):
            raise ValueError("request needs a query or task/review text to retrieve for")
        return self


class ChunkScores(BaseModel):
    lexical: float | None = None
    lexical_rank: int | None = None
    vector: float | None = None
    vector_rank: int | None = None
    symbol: float | None = None
    symbol_rank: int | None = None
    fused: float | None = None
    fused_rank: int | None = None
    reranker: float | None = None
    reranker_raw: float | None = None
    reranker_rank: int | None = None
    deterministic: float | None = None
    final: float = 0.0


class RetrievedChunk(BaseModel):
    rank: int
    chunk_id: str
    corpus_id: str
    source_type: str
    authority: str
    path: str
    document: str
    symbol: str | None = None
    symbol_kind: str | None = None
    section: str | None = None
    language: str | None = None
    start_line: int
    end_line: int
    content: str
    content_hash: str
    file_hash: str
    version: str
    snapshot_id: str | None = None
    generation: int
    indexed_at: float
    token_count: int
    freshness: Literal["verified", "indexed"]
    scores: ChunkScores
    features: dict[str, float] = Field(default_factory=dict)


class CacheInfo(BaseModel):
    status: Literal["miss", "hit", "semantic_hit", "revalidated_hit", "bypass", "refresh"]
    key: str | None = None
    reason: str | None = None
    source_request_id: str | None = None


class RetrieveResponse(BaseModel):
    request_id: str
    profile: str
    config_id: str
    query: dict[str, Any]
    cache: CacheInfo
    snapshots: dict[str, dict[str, Any]]
    degraded: bool
    degraded_reasons: list[str]
    chunks: list[RetrievedChunk]
    context: str
    context_tokens: int
    stats: dict[str, Any]
    timings_ms: dict[str, float]
    candidates: list[dict[str, Any]] | None = None


def _index_source_types() -> list[Literal["code", "doc"]]:
    return ["code", "doc"]


class IndexDocument(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=2_000_000)
    version: str | None = Field(None, max_length=128)
    title: str | None = None
    source_url: str | None = None
    valid_until: datetime | None = None


class IndexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    corpus_id: str = Field(pattern=CORPUS_ID_PATTERN)
    root: str | None = None
    mode: Literal["incremental", "full"] = "incremental"
    paths: list[str] | None = None
    source_types: list[Literal["code", "doc"]] = Field(default_factory=_index_source_types)
    include_globs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)
    documents: list[IndexDocument] | None = None
    documents_mode: Literal["upsert", "replace"] = "upsert"
    force: bool = False


class IndexResponse(BaseModel):
    corpus_id: str
    code_generation: int
    docs_generation: int
    code_snapshot: str | None
    docs_snapshot: str | None
    git_head: str | None
    files: dict[str, int]
    chunks: dict[str, int]
    embeddings: dict[str, int]
    degraded_reasons: list[str]
    duration_ms: float


class InvalidateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    corpus_id: str = Field(pattern=CORPUS_ID_PATTERN)
    paths: list[str] | None = None
    chunk_ids: list[str] | None = None
    source_type: Literal["code", "doc"] | None = None
    all: bool = False
    reason: str = Field("manual", max_length=200)
    reindex: bool = False

    @model_validator(mode="after")
    def _selector(self) -> InvalidateRequest:
        if not (self.all or self.paths or self.chunk_ids):
            raise ValueError("specify paths, chunk_ids or all=true")
        return self


class InvalidateResponse(BaseModel):
    corpus_id: str
    invalidated_chunks: int
    invalidated_files: int
    code_generation: int
    docs_generation: int
    reindex: IndexResponse | None = None
