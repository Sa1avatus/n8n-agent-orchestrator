"""Environment-driven service settings. Secrets are read from the environment only."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]


@dataclass
class Settings:
    data_dir: Path = Path("./var")
    allowed_roots: list[str] = field(default_factory=list)
    api_key: str | None = None
    embedder: str = "hashing"
    embedding_dim: int = 384
    embedding_url: str | None = None
    embedding_model: str | None = None
    embedding_api_key: str | None = None
    embedding_query_prefix: str = ""
    embedding_passage_prefix: str = ""
    embedding_batch_size: int = 32
    reranker_url: str | None = None
    reranker_api_key: str | None = None
    reranker_timeout_seconds: float = 10.0
    reranker_max_chars: int = 4000
    profiles_file: str | None = None
    max_file_bytes: int = 512 * 1024
    cache_ttl_seconds: float = 24 * 3600
    cache_max_entries: int = 5000
    log_query_text: bool = True
    sync_min_interval_seconds: float = 2.0
    backend: str = "auto"
    rag_url: str | None = None
    rag_api_key: str | None = None
    rag_owner_id: str | None = None
    rag_project_id: str | None = None
    rag_collection: str | None = None
    rag_timeout_seconds: float = 5.0
    rag_cooldown_seconds: float = 30.0
    rag_mirror: bool = True
    rag_fresh_window_seconds: float = 120.0

    @property
    def rag_configured(self) -> bool:
        return bool(
            self.rag_url
            and self.rag_api_key
            and self.rag_owner_id
            and self.rag_project_id
            and self.rag_collection
        )

    @property
    def store_path(self) -> Path:
        return self.data_dir / "index.sqlite"

    @property
    def log_path(self) -> Path:
        return self.data_dir / "retrieval_logs.sqlite"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = dict(os.environ if env is None else env)
        return cls(
            data_dir=Path(e.get("RETRIEVAL_DATA_DIR", "./var")),
            allowed_roots=_list(e.get("RETRIEVAL_ALLOWED_ROOTS")),
            api_key=e.get("RETRIEVAL_API_KEY") or None,
            embedder=e.get("RETRIEVAL_EMBEDDER", "hashing"),
            embedding_dim=int(e.get("RETRIEVAL_EMBEDDING_DIM", "384")),
            embedding_url=e.get("RETRIEVAL_EMBEDDING_URL") or None,
            embedding_model=e.get("RETRIEVAL_EMBEDDING_MODEL") or None,
            embedding_api_key=e.get("RETRIEVAL_EMBEDDING_API_KEY") or None,
            embedding_query_prefix=e.get("RETRIEVAL_EMBEDDING_QUERY_PREFIX", ""),
            embedding_passage_prefix=e.get("RETRIEVAL_EMBEDDING_PASSAGE_PREFIX", ""),
            embedding_batch_size=int(e.get("RETRIEVAL_EMBEDDING_BATCH_SIZE", "32")),
            reranker_url=e.get("RETRIEVAL_RERANKER_URL") or None,
            reranker_api_key=e.get("RETRIEVAL_RERANKER_API_KEY") or None,
            reranker_timeout_seconds=float(e.get("RETRIEVAL_RERANKER_TIMEOUT_SECONDS", "10")),
            reranker_max_chars=int(e.get("RETRIEVAL_RERANKER_MAX_CHARS", "4000")),
            profiles_file=e.get("RETRIEVAL_PROFILES_FILE") or None,
            max_file_bytes=int(e.get("RETRIEVAL_MAX_FILE_BYTES", str(512 * 1024))),
            cache_ttl_seconds=float(e.get("RETRIEVAL_CACHE_TTL_SECONDS", str(24 * 3600))),
            cache_max_entries=int(e.get("RETRIEVAL_CACHE_MAX_ENTRIES", "5000")),
            log_query_text=_bool(e.get("RETRIEVAL_LOG_QUERY_TEXT"), True),
            sync_min_interval_seconds=float(e.get("RETRIEVAL_SYNC_MIN_INTERVAL_SECONDS", "2")),
            backend=e.get("RETRIEVAL_BACKEND", "auto"),
            rag_url=e.get("RETRIEVAL_RAG_URL") or None,
            rag_api_key=e.get("RETRIEVAL_RAG_API_KEY") or None,
            rag_owner_id=e.get("RETRIEVAL_RAG_OWNER_ID") or None,
            rag_project_id=e.get("RETRIEVAL_RAG_PROJECT_ID") or None,
            rag_collection=e.get("RETRIEVAL_RAG_COLLECTION") or None,
            rag_timeout_seconds=float(e.get("RETRIEVAL_RAG_TIMEOUT_SECONDS", "5")),
            rag_cooldown_seconds=float(e.get("RETRIEVAL_RAG_COOLDOWN_SECONDS", "30")),
            rag_mirror=_bool(e.get("RETRIEVAL_RAG_MIRROR"), True),
            rag_fresh_window_seconds=float(e.get("RETRIEVAL_RAG_FRESH_WINDOW_SECONDS", "120")),
        )
