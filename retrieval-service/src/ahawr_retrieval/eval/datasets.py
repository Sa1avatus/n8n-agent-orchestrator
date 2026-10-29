"""Evaluation dataset and configuration schemas.

* **Gold** datasets are small and manually verified: every judgment was checked by a person
  against a pinned repository snapshot.
* **Silver** datasets are generated from historical AHAWR tasks (accepted solutions, changed
  files, evidence). They are weak ground truth: a Reviewer ``pass`` is observed evidence, not
  proof of correctness, so silver judgments are marked ``weak`` and reported separately.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .metrics import Judgment

_REAL_JSONL = Path(__file__).resolve().parents[3] / "eval" / "data" / "real_queries.jsonl"


class JudgmentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    symbol: str | None = None
    section: str | None = None
    chunk_id: str | None = None
    grade: int = Field(1, ge=0, le=3)
    note: str | None = None

    def to_judgment(self) -> Judgment:
        return Judgment(self.path, self.grade, self.symbol, self.section, self.chunk_id)


class VariantSpec(BaseModel):
    """A retry-like rephrasing of the task used to measure cache reuse decisions."""

    model_config = ConfigDict(extra="forbid")
    id: str
    feedback: str
    expect: Literal["reuse", "reretrieve"]


def _both_source_types() -> list[Literal["code", "doc"]]:
    return ["code", "doc"]


class CorpusSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    corpus_id: str
    root: str
    source_types: list[Literal["code", "doc"]] = Field(default_factory=_both_source_types)
    exclude_globs: list[str] = Field(default_factory=list)


class EvalTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    profile: str = "worker"
    task: dict[str, Any] = Field(default_factory=dict)
    review: dict[str, Any] | None = None
    query: str | None = None
    relevant: list[JudgmentSpec] = Field(min_length=1)
    variants: list[VariantSpec] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    weak: bool = False
    notes: str = ""

    def judgments(self) -> list[Judgment]:
        return [j.to_judgment() for j in self.relevant]


class EvalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: str
    version: str
    kind: Literal["gold", "silver"]
    description: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)
    corpora: list[CorpusSpec] = Field(min_length=1)
    tasks: list[EvalTask] = Field(min_length=1)
    # k values the runner must report; the 'real' dataset pins k=5 and k=8.
    ks: list[int] = Field(default_factory=lambda: [1, 3, 5, 10])

    @classmethod
    def load(cls, path: str | Path) -> EvalDataset:
        file = Path(path)
        # Register the 'real' dataset by name: the JSONL shipped under eval/data/
        if str(file) == "real" or file.name == "real":
            file = _REAL_JSONL
            if not file.is_file():
                raise FileNotFoundError(f"real dataset not found: {file}")
            return cls.load_jsonl(file)
        if file.suffixes == [".jsonl"] or str(file).endswith(".jsonl"):
            return cls.load_jsonl(file)
        dataset = cls.model_validate_json(file.read_text(encoding="utf-8"))
        for corpus in dataset.corpora:
            root = Path(corpus.root)
            if not root.is_absolute():
                corpus.root = str((file.parent / root).resolve())
        return dataset

    @classmethod
    def load_jsonl(cls, file: Path) -> EvalDataset:
        records: list[dict[str, Any]] = []
        with file.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        # expected paths are repo-relative: repo root is three levels above eval/data/
        repo_root = file.resolve().parents[3]
        tasks: list[EvalTask] = []
        for r in records:
            paths = r.get("expected_paths") or []
            if not paths:
                raise ValueError(f"record {r.get('id', '<unknown>')} has no expected_paths")
            tasks.append(
                EvalTask(
                    id=str(r["id"]),
                    profile=str(r.get("role", "worker")),
                    query=str(r.get("query", "")),
                    relevant=[JudgmentSpec(path=p, grade=1) for p in paths],
                    tags=[str(r.get("query_type", "words"))],
                    weak=True,
                )
            )
        return cls(
            dataset_id="real",
            version="1",
            kind="silver",
            description=f"Real-query dataset loaded from {file.name} ({len(tasks)} tasks)",
            provenance={"source": str(file.name), "kind": "jsonl"},
            corpora=[CorpusSpec(corpus_id="repo", root=str(repo_root))],
            tasks=tasks,
            ks=[5, 8],
        )


class EvalConfig(BaseModel):
    """One retrieval configuration under test. ``options`` are profile overrides."""

    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""
    options: dict[str, Any] = Field(default_factory=dict)
    embedder: dict[str, Any] = Field(default_factory=lambda: {"kind": "hashing"})
    reranker_url: str | None = None
    reranker_api_key: str | None = None
    # built-in CPU cross-encoder (fastembed model name); used when reranker_url is empty
    reranker_model: str | None = None
    rag: dict[str, Any] | None = None

    @classmethod
    def load(cls, path: str | Path) -> EvalConfig:
        raw = _interpolate(json.loads(Path(path).read_text(encoding="utf-8")))
        return cls.model_validate(raw)


_ENV = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]*))?\}")


def _interpolate(value: Any) -> Any:
    """Resolve ``${VAR}`` / ``${VAR:-default}`` so secrets stay out of config files."""
    if isinstance(value, str):
        if "${" not in value:
            return value
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value) or None
    if isinstance(value, list):
        return [_interpolate(v) for v in value]
    if isinstance(value, dict):
        return {k: _interpolate(v) for k, v in value.items()}
    return value
