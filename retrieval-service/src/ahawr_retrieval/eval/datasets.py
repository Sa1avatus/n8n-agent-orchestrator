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

    @classmethod
    def load(cls, path: str | Path) -> EvalDataset:
        file = Path(path)
        dataset = cls.model_validate_json(file.read_text(encoding="utf-8"))
        for corpus in dataset.corpora:
            root = Path(corpus.root)
            if not root.is_absolute():
                corpus.root = str((file.parent / root).resolve())
        return dataset


class EvalConfig(BaseModel):
    """One retrieval configuration under test. ``options`` are profile overrides."""

    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = ""
    options: dict[str, Any] = Field(default_factory=dict)
    embedder: dict[str, Any] = Field(default_factory=lambda: {"kind": "hashing"})
    reranker_url: str | None = None
    reranker_api_key: str | None = None
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
