"""Deterministic hard constraints.

Validity, freshness and snapshot rules are never delegated to the reranker or to weighted
scores: a chunk that fails any rule is removed from the candidate set (and logged with the
reason) regardless of how relevant it looks.
"""

from __future__ import annotations

import fnmatch
import time
from collections.abc import Iterable

from .candidates import Candidate
from .indexer import WorkspaceVerifier
from .models import WorkspaceState
from .profiles import Profile
from .store import ACTIVE, CorpusRecord


def apply_hard_filters(
    candidates: Iterable[Candidate],
    *,
    corpora: dict[str, CorpusRecord],
    profile: Profile,
    workspace_state: WorkspaceState | None,
    freshness_mode: str,
    verifier: WorkspaceVerifier,
    now: float | None = None,
) -> dict[str, int]:
    """Set ``filtered_reason`` on failing candidates; return counts per reason."""
    now = time.time() if now is None else now
    reasons: dict[str, int] = {}
    allowed_types: set[str] = set(profile.source_types)
    for candidate in candidates:
        reason = _check(
            candidate,
            corpora,
            profile,
            allowed_types,
            workspace_state,
            freshness_mode,
            verifier,
            now,
        )
        if reason:
            candidate.filtered_reason = reason
            reasons[reason] = reasons.get(reason, 0) + 1
    return reasons


def _check(
    candidate: Candidate,
    corpora: dict[str, CorpusRecord],
    profile: Profile,
    allowed_types: set[str],
    workspace_state: WorkspaceState | None,
    freshness_mode: str,
    verifier: WorkspaceVerifier,
    now: float,
) -> str | None:
    record, file = candidate.record, candidate.file
    if record is None:
        return "missing"
    if record.status != ACTIVE:
        return f"chunk_{record.status}"
    if record.source_type not in allowed_types:
        return "source_type_excluded"
    if any(fnmatch.fnmatch(record.path, pattern) for pattern in profile.exclude_globs):
        return "path_excluded"
    if file is None:
        return "file_missing"
    if file.status != ACTIVE:
        return f"file_{file.status}"
    if record.file_hash != file.file_hash:
        return "stale_chunk"
    if record.version != file.version:
        return "version_mismatch"
    if file.valid_until is not None and file.valid_until < now:
        return "doc_expired"
    corpus = corpora.get(record.corpus_id)
    if workspace_state is not None:
        pinned = workspace_state.doc_versions.get(record.path)
        if record.source_type == "doc" and pinned is not None and pinned != record.version:
            return "doc_version_mismatch"
        if workspace_state.strict and corpus is not None:
            expected = (
                workspace_state.code_snapshot
                if record.source_type == "code"
                else workspace_state.docs_snapshot
            )
            if expected and expected != corpus.snapshot(record.source_type):
                return "snapshot_mismatch"
    if (
        freshness_mode in {"verify", "sync"}
        and file.origin == "workspace"
        and corpus
        and corpus.root
    ):
        current = verifier.current_hash(corpus.root, record.path)
        if current is None:
            return "file_deleted"
        if current != record.file_hash:
            return "file_changed"
        candidate.freshness = "verified"
    return None
