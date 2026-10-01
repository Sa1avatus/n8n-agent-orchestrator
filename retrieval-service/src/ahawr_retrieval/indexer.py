"""Incremental indexing, chunk-level invalidation and workspace freshness verification.

Version / freshness model
-------------------------
* **Code** is versioned by *workspace snapshot*. Every code chunk carries the ``file_hash`` of the
  file it was cut from and the ``snapshot_id`` (a hash over all indexed code file hashes) observed
  when it was last confirmed. A code chunk is fresh only while the file on disk still has that
  hash.
* **Documentation** is versioned per document: ``version`` is the caller-supplied document
  version (inline docs) or the file hash (repository docs), with an optional ``valid_until``.
  A doc chunk is fresh only while its document's current version equals the chunk's version and
  ``valid_until`` has not passed.

Code and docs keep separate generation counters, so a code edit never invalidates cached docs
retrieval and vice versa. Within a file, chunks are diffed by stable ``chunk_id`` and
``content_hash``: unchanged chunks keep their rows and embeddings; only added/modified/deleted
chunks are written and recorded in the change log.
"""

from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from .chunking import ChunkedFile, ChunkingConfig, SymbolDef, chunk_file, classify_path
from .config import Settings
from .embeddings import Embedder, EmbeddingError
from .models import (
    IndexDocument,
    IndexRequest,
    IndexResponse,
    IndexStale,
    InvalidateRequest,
    InvalidateResponse,
)
from .store import ACTIVE, DELETED, INVALIDATED, ChunkRecord, CorpusRecord, FileRecord, Store
from .text import estimate_tokens, expand_for_index, sha256_hex, short_hash, split_identifier
from .vector_index import VectorIndex

CHUNKER_VERSION = "chunker-v2"  # v2: Dockerfile.*, patches, CMake; agent plans and backups excluded
EMBED_BACKFILL_LIMIT = 2000
EMBED_BACKOFF_SECONDS = 60.0

EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        "target",
        "coverage",
        ".idea",
        ".vscode",
        ".next",
        ".nuxt",
        ".terraform",
        "site-packages",
        ".cache",
        "secrets",
        "credentials",
        "tokens",
        ".eggs",
        "htmlcov",
    }
)
# Never index secrets or generated artefacts (the Worker prompt forbids reading .env and secrets).
EXCLUDED_FILES = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "*.keystore",
    "*.jks",
    "credentials*.json",
    "secrets.*",
    "*.secret",
    "*.sqlite",
    "*.sqlite3",
    "*.db",
    "*.log",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "uv.lock",
    "Cargo.lock",
    "go.sum",
    "*.min.js",
    "*.min.css",
    "*.map",
)
ALLOWED_DOTFILES = frozenset({".env.example"})
# Backup/edit artefacts are never indexed by default. ``Settings.exclude_globs`` (from
# RETRIEVAL_EXCLUDE_GLOBS, comma-separated) replaces this list; pass per-request or per-corpora
# ``exclude_globs`` to extend/override it further.
DEFAULT_EXCLUDE_GLOBS = (
    "*.bak",
    "*.bak-*",
    "*.orig",
    "*.rej",
    "*~",
    "*_backup*",
    "*backup[0-9]*",
    "*.old",
)


class IndexingError(ValueError):
    """Client error: invalid root, path or request."""


@dataclass
class _Stats:
    files: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(
            ("scanned", "unchanged", "added", "updated", "deleted", "revalidated", "skipped"), 0
        )
    )
    chunks: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(
            ("added", "modified", "deleted", "unchanged", "revalidated"), 0
        )
    )
    embeddings: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(("computed", "reused", "pending"), 0)
    )
    degraded: list[str] = field(default_factory=list)
    changed_types: set[str] = field(default_factory=set)
    embed_hashes: dict[str, str] = field(default_factory=dict)


def is_excluded(rel_path: str, extra_excludes: list[str] | None = None) -> bool:
    parts = PurePosixPath(rel_path).parts
    if any(part in EXCLUDED_DIRS for part in parts[:-1]):
        return True
    name = parts[-1] if parts else rel_path
    if name not in ALLOWED_DOTFILES and any(
        fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in EXCLUDED_FILES
    ):
        return True
    return any(
        fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(name, pattern)
        for pattern in extra_excludes or []
    )


def git_files(root: Path) -> list[str] | None:
    """Tracked and untracked-but-not-ignored files under ``root`` as git sees them, or None when
    ``root`` is not inside a git work tree or git is unavailable."""
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(
            # safe.directory: the workspace is a read-only mount owned by another user
            [
                "git",
                "-c",
                "safe.directory=*",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            timeout=120,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    names = result.stdout.decode("utf-8", errors="replace").split("\0")
    return sorted({name for name in names if name})


def normalize_rel_path(path: str) -> str:
    rel = path.replace("\\", "/").strip()
    while rel.startswith("./"):
        rel = rel[2:]
    rel = rel.lstrip("/")
    pure = PurePosixPath(rel)
    if not rel or any(part == ".." for part in pure.parts):
        raise IndexingError(f"invalid relative path: {path!r}")
    return str(pure)


def chunk_id_for(corpus_id: str, source_type: str, path: str, anchor: str) -> str:
    return "ch_" + short_hash(corpus_id, source_type, path, anchor)


def read_git_head(root: Path) -> str | None:
    head = root / ".git" / "HEAD"
    try:
        content = head.read_text(encoding="utf-8").strip()
        if not content.startswith("ref:"):
            return content[:40] or None
        ref = content.split(":", 1)[1].strip()
        ref_file = root / ".git" / ref
        if ref_file.is_file():
            return ref_file.read_text(encoding="utf-8").strip()[:40] or None
        packed = root / ".git" / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0][:40]
    except OSError:
        return None
    return None


class WorkspaceVerifier:
    """Hashes files on demand (memoized by mtime/size) to verify chunk freshness at read time."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[int, int, str]] = {}
        self._lock = threading.Lock()

    def current_hash(self, root: str, rel_path: str) -> str | None:
        absolute = os.path.join(root, rel_path)
        try:
            stat = os.stat(absolute)
        except OSError:
            return None
        with self._lock:
            cached = self._cache.get(absolute)
            if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
                return cached[2]
        try:
            digest = sha256_hex(Path(absolute).read_bytes())
        except OSError:
            return None
        with self._lock:
            self._cache[absolute] = (stat.st_mtime_ns, stat.st_size, digest)
        return digest


class Indexer:
    def __init__(
        self,
        store: Store,
        embedder: Embedder,
        vector_index: VectorIndex,
        settings: Settings,
        chunking: ChunkingConfig | None = None,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.vector_index = vector_index
        self.settings = settings
        self.chunking = chunking or ChunkingConfig()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def _lock_for(self, corpus_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(corpus_id, threading.Lock())

    # ----------------------------------------------------------------- public

    def resolve_root(self, root: str) -> Path:
        resolved = Path(root).expanduser().resolve()
        allowed = [Path(r).expanduser().resolve() for r in self.settings.allowed_roots]
        if not allowed:
            raise IndexingError(
                "workspace indexing is disabled: set RETRIEVAL_ALLOWED_ROOTS to permit roots"
            )
        if not any(resolved == a or a in resolved.parents for a in allowed):
            raise IndexingError(f"root {root!r} is outside RETRIEVAL_ALLOWED_ROOTS")
        if not resolved.is_dir():
            raise IndexingError(f"root {root!r} is not a directory")
        return resolved

    def index(self, request: IndexRequest) -> IndexResponse:
        started = time.perf_counter()
        existing = self.store.get_corpus(request.corpus_id)
        if request.root:
            try:
                root = self.resolve_root(request.root)
            except IndexingError:
                raise
        elif existing is not None and existing.root:
            try:
                root = self.resolve_root(existing.root)
            except IndexingError as exc:
                if "not a directory" in str(exc):
                    self._mark_corpus_stale(request.corpus_id)
                    message = (
                        f"corpus {request.corpus_id!r} is stale: root {existing.root!r} is gone"
                    )
                    raise IndexStale(message) from exc
                raise
        else:
            root = None
            if not request.documents:
                raise IndexingError("root is required for the first index of a workspace corpus")
        config: dict[str, Any] = {}
        if request.include_globs:
            config["include_globs"] = request.include_globs
        # ``exclude_globs`` (request, then stored corpus config, then RETRIEVAL_EXCLUDE_GLOBS,
        # then DEFAULT_EXCLUDE_GLOBS): a non-empty value replaces the level below it, so a
        # request/corpus glob overrides both the environment variable and the default list.
        if request.exclude_globs:
            config["exclude_globs"] = request.exclude_globs
        elif existing is not None and existing.config.get("exclude_globs"):
            config["exclude_globs"] = list(existing.config["exclude_globs"])
        elif self.settings.exclude_globs:
            config["exclude_globs"] = list(self.settings.exclude_globs)
        else:
            config["exclude_globs"] = list(DEFAULT_EXCLUDE_GLOBS)
        stats = _Stats()
        with self._lock_for(request.corpus_id):
            corpus = self.store.ensure_corpus(
                request.corpus_id, str(root) if root else None, config
            )
            force = request.force or corpus.config.get("chunker_version") not in (
                None,
                CHUNKER_VERSION,
            )
            if root is not None:
                self._sync_workspace(
                    corpus,
                    root,
                    stats,
                    full=request.mode == "full",
                    paths=request.paths,
                    source_types=set(request.source_types),
                    force=force,
                )
            if request.documents is not None:
                self._sync_documents(
                    corpus, request.documents, request.documents_mode, stats, force
                )
            self._finish(corpus, root, stats)
        stale = corpus.stale
        return self._response(request.corpus_id, stats, started, stale=stale)

    def sync(self, corpus_id: str, paths: list[str] | None = None) -> IndexResponse | None:
        """Incremental workspace sync used by ``freshness_mode=sync`` before retrieval."""
        corpus = self.store.get_corpus(corpus_id)
        if corpus is None or not corpus.root:
            return None
        if (
            paths is None
            and corpus.last_sync_at is not None
            and (time.time() - corpus.last_sync_at < self.settings.sync_min_interval_seconds)
        ):
            return None
        return self.index(IndexRequest(corpus_id=corpus_id, paths=paths))

    def invalidate(self, request: InvalidateRequest) -> InvalidateResponse:
        corpus = self.store.get_corpus(request.corpus_id)
        if corpus is None:
            raise IndexingError(f"unknown corpus {request.corpus_id!r}")
        gens = {"code": corpus.code_generation + 1, "doc": corpus.docs_generation + 1}
        touched_types: set[str] = set()
        paths_touched: set[str] = set()
        invalidated_chunks = 0
        invalidated_files = 0
        with self._lock_for(request.corpus_id), self.store.transaction() as conn:
            clauses = ["corpus_id = ?", "status = 'active'"]
            params: list[Any] = [request.corpus_id]
            if request.source_type:
                clauses.append("source_type = ?")
                params.append(request.source_type)
            selectors = []
            if request.paths:
                norm = [normalize_rel_path(p) for p in request.paths]
                selectors.append(f"path IN ({','.join('?' * len(norm))})")
                params.extend(norm)
            if request.chunk_ids:
                selectors.append(f"chunk_id IN ({','.join('?' * len(request.chunk_ids))})")
                params.extend(request.chunk_ids)
            if selectors and not request.all:
                clauses.append("(" + " OR ".join(selectors) + ")")
            rows = conn.execute(
                "SELECT * FROM chunks WHERE " + " AND ".join(clauses), params
            ).fetchall()
            for row in rows:
                record = ChunkRecord(**dict(row))
                gen = gens["code" if record.source_type == "code" else "doc"]
                self.store.deactivate_chunk(conn, record, INVALIDATED, request.reason, gen)
                self.store.record_change(
                    conn,
                    record.corpus_id,
                    record.source_type,
                    gen,
                    record.chunk_id,
                    record.path,
                    "invalidated",
                )
                touched_types.add(record.source_type)
                paths_touched.add(record.path)
                invalidated_chunks += 1
            if request.paths or request.all:
                file_clauses = ["corpus_id = ?", "status = 'active'"]
                file_params: list[Any] = [request.corpus_id]
                if request.source_type:
                    file_clauses.append("source_type = ?")
                    file_params.append(request.source_type)
                if request.paths and not request.all:
                    norm = [normalize_rel_path(p) for p in request.paths]
                    file_clauses.append(f"path IN ({','.join('?' * len(norm))})")
                    file_params.extend(norm)
                invalidated_files = conn.execute(
                    "UPDATE files SET status = 'invalidated', status_reason = ? WHERE "
                    + " AND ".join(file_clauses),
                    (request.reason, *file_params),
                ).rowcount
            updates: dict[str, Any] = {}
            if "code" in touched_types:
                updates["code_generation"] = gens["code"]
            if touched_types - {"code"}:
                updates["docs_generation"] = gens["doc"]
            if updates:
                sets = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(
                    f"UPDATE corpora SET {sets}, updated_at = ? WHERE corpus_id = ?",
                    (*updates.values(), time.time(), request.corpus_id),
                )
        reindex = None
        if request.reindex and corpus.root and paths_touched:
            reindex = self._reindex_paths(corpus.corpus_id, sorted(paths_touched))
        current = self.store.get_corpus(request.corpus_id)
        assert current is not None
        return InvalidateResponse(
            corpus_id=request.corpus_id,
            invalidated_chunks=invalidated_chunks,
            invalidated_files=int(invalidated_files),
            code_generation=current.code_generation,
            docs_generation=current.docs_generation,
            reindex=reindex,
        )

    def backfill_embeddings(self, corpus_id: str, limit: int = 5000) -> int:
        pending = self.store.chunks_missing_embeddings(self.embedder.model_id, corpus_id, limit)
        if not pending:
            return 0
        vectors = self.embedder.embed_documents([content for _, content in pending])
        self.store.put_embeddings(
            self.embedder.model_id, {h: vectors[i] for i, (h, _) in enumerate(pending)}
        )
        self.vector_index.invalidate(corpus_id)
        return len(pending)

    # --------------------------------------------------------------- internal

    def _reindex_paths(self, corpus_id: str, paths: list[str]) -> IndexResponse:
        started = time.perf_counter()
        corpus = self.store.get_corpus(corpus_id)
        assert corpus is not None and corpus.root
        root = self.resolve_root(corpus.root)
        stats = _Stats()
        with self._lock_for(corpus_id):
            known = self.store.get_files(corpus_id, paths)
            workspace_paths = [p for p in paths if p not in known or known[p].origin == "workspace"]
            self._sync_workspace(
                corpus,
                root,
                stats,
                full=True,
                paths=workspace_paths,
                source_types={"code", "doc"},
                force=False,
                rechunk=set(workspace_paths),
            )
            self._finish(corpus, root, stats)
        return self._response(corpus_id, stats, started)

    def _walk(self, root: Path, extra_excludes: list[str], includes: list[str]) -> list[str]:
        """Every text file of the workspace except what .gitignore excludes. In a git work tree
        git itself lists the files (all .gitignore levels, negations, .git/info/exclude);
        otherwise the root .gitignore is applied approximately."""
        listed = git_files(root)
        if listed is not None:
            return [
                rel
                for rel in listed
                if not any(part in EXCLUDED_DIRS for part in rel.split("/")[:-1])
                and not is_excluded(rel, extra_excludes)
                and (not includes or any(fnmatch.fnmatch(rel, p) for p in includes))
                and classify_path(rel) is not None
                and not os.path.islink(root / rel)
            ]
        ignore_patterns = self._gitignore(root)
        result: list[str] = []
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
            dirnames[:] = sorted(
                d
                for d in dirnames
                if d not in EXCLUDED_DIRS
                and not os.path.islink(os.path.join(dirpath, d))
                and not self._ignored(f"{rel_dir}/{d}".lstrip("/") + "/", ignore_patterns)
            )
            for name in sorted(filenames):
                rel = f"{rel_dir}/{name}".lstrip("/")
                if os.path.islink(os.path.join(dirpath, name)):
                    continue
                if is_excluded(rel, extra_excludes) or self._ignored(rel, ignore_patterns):
                    continue
                if includes and not any(fnmatch.fnmatch(rel, pattern) for pattern in includes):
                    continue
                if classify_path(rel) is None:
                    continue
                result.append(rel)
        return result

    @staticmethod
    def _gitignore(root: Path) -> list[str]:
        path = root / ".gitignore"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [line.strip() for line in lines if line.strip() and not line.startswith(("#", "!"))]

    @staticmethod
    def _ignored(rel: str, patterns: list[str]) -> bool:
        """Approximate .gitignore matching (no negation); ``rel`` ends with '/' for dirs."""
        is_dir = rel.endswith("/")
        clean = rel.rstrip("/")
        parts = clean.split("/")
        for pattern in patterns:
            dir_only = pattern.endswith("/")
            pat = pattern.strip("/")
            if not pat:
                continue
            if "/" in pat:
                if clean.startswith(pat + "/") or (
                    fnmatch.fnmatch(clean, pat) and (is_dir or not dir_only)
                ):
                    return True
                continue
            if any(fnmatch.fnmatch(part, pat) for part in (parts if is_dir else parts[:-1])):
                return True
            if not dir_only and not is_dir and fnmatch.fnmatch(parts[-1], pat):
                return True
        return False

    def _sync_workspace(
        self,
        corpus: CorpusRecord,
        root: Path,
        stats: _Stats,
        *,
        full: bool,
        paths: list[str] | None,
        source_types: set[str],
        force: bool,
        rechunk: set[str] | None = None,
    ) -> None:
        manifest = self.store.files(corpus.corpus_id, origin="workspace")
        excludes = list(corpus.config.get("exclude_globs", []))
        includes = list(corpus.config.get("include_globs", []))
        if paths is not None:
            candidates = [normalize_rel_path(p) for p in paths]
            candidates = [p for p in candidates if not is_excluded(p, excludes)]
            scope_deleted = [p for p in candidates if not (root / p).is_file()]
        else:
            candidates = self._walk(root, excludes, includes)
            seen = set(candidates)
            scope_deleted = [
                p for p, f in manifest.items() if p not in seen and f.status != DELETED
            ]
        gens = {"code": corpus.code_generation + 1, "doc": corpus.docs_generation + 1}
        for rel in candidates:
            kind = classify_path(rel)
            if kind is None or kind[0] not in source_types:
                continue
            absolute = root / rel
            if not absolute.is_file():
                continue
            stats.files["scanned"] += 1
            try:
                stat = absolute.stat()
            except OSError:
                continue
            previous = manifest.get(rel)
            must_rechunk = force or (rechunk is not None and rel in rechunk)
            # Invalidation persists until the content changes or the path is explicitly
            # re-indexed (``paths``/``/invalidate reindex``/``force``); a routine sync keeps it.
            revalidate = must_rechunk or paths is not None
            keep_invalid = (
                previous is not None and previous.status == INVALIDATED and not revalidate
            )
            if (
                previous is not None
                and (previous.status == ACTIVE or keep_invalid)
                and not full
                and not must_rechunk
                and previous.size == stat.st_size
                and previous.mtime_ns == stat.st_mtime_ns
            ):
                stats.files["unchanged"] += 1
                continue
            if stat.st_size > self.settings.max_file_bytes:
                stats.files["skipped"] += 1
                if previous is not None and previous.status != DELETED:
                    self._delete_file(corpus, previous, "excluded_too_large", gens, stats)
                continue
            try:
                raw = absolute.read_bytes()
            except OSError:
                stats.files["skipped"] += 1
                continue
            if b"\x00" in raw[:8192]:
                stats.files["skipped"] += 1
                continue
            file_hash = sha256_hex(raw)
            if (
                previous is not None
                and previous.file_hash == file_hash
                and (previous.status == ACTIVE or keep_invalid)
                and not must_rechunk
            ):
                # Same content, new mtime: refresh the manifest only.
                self._touch_file(previous, stat.st_size, stat.st_mtime_ns)
                stats.files["unchanged"] += 1
                continue
            text = raw.decode("utf-8", errors="replace")
            self._apply_file(
                corpus,
                FileRecord(
                    corpus_id=corpus.corpus_id,
                    path=rel,
                    source_type=kind[0],
                    origin="workspace",
                    language=kind[1],
                    file_hash=file_hash,
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    version=file_hash[:16],
                    status=ACTIVE,
                    status_reason=None,
                    snapshot_id=None,
                    title=None,
                    source_url=None,
                    valid_until=None,
                    indexed_at=time.time(),
                ),
                text,
                gens,
                stats,
                previous,
                revalidate=revalidate,
            )
        for rel in scope_deleted:
            previous = manifest.get(rel)
            if previous is not None and previous.status != DELETED:
                self._delete_file(corpus, previous, "file_deleted", gens, stats)

    def _sync_documents(
        self,
        corpus: CorpusRecord,
        documents: list[IndexDocument],
        mode: str,
        stats: _Stats,
        force: bool,
    ) -> None:
        manifest = self.store.files(corpus.corpus_id, origin="inline")
        gens = {"code": corpus.code_generation + 1, "doc": corpus.docs_generation + 1}
        seen: set[str] = set()
        for document in documents:
            rel = normalize_rel_path(document.path)
            seen.add(rel)
            stats.files["scanned"] += 1
            content_hash = sha256_hex(document.content)
            version = document.version or content_hash[:16]
            valid_until = document.valid_until.timestamp() if document.valid_until else None
            previous = manifest.get(rel)
            if (
                previous is not None
                and previous.status == ACTIVE
                and not force
                and previous.file_hash == content_hash
                and previous.version == version
                and previous.valid_until == valid_until
            ):
                stats.files["unchanged"] += 1
                continue
            kind = classify_path(rel)
            language = kind[1] if kind and kind[0] == "doc" else "markdown"
            self._apply_file(
                corpus,
                FileRecord(
                    corpus_id=corpus.corpus_id,
                    path=rel,
                    source_type="doc",
                    origin="inline",
                    language=language,
                    file_hash=content_hash,
                    size=len(document.content),
                    mtime_ns=0,
                    version=version,
                    status=ACTIVE,
                    status_reason=None,
                    snapshot_id=None,
                    title=document.title,
                    source_url=document.source_url,
                    valid_until=valid_until,
                    indexed_at=time.time(),
                ),
                document.content,
                gens,
                stats,
                previous,
                revalidate=True,
            )
        if mode == "replace":
            for rel, previous in manifest.items():
                if rel not in seen and previous.status != DELETED:
                    self._delete_file(corpus, previous, "document_removed", gens, stats)

    def _touch_file(self, previous: FileRecord, size: int, mtime_ns: int) -> None:
        previous.size = size
        previous.mtime_ns = mtime_ns
        with self.store.transaction() as conn:
            self.store.upsert_file(conn, previous)

    def _chunk(self, record: FileRecord, text: str) -> ChunkedFile:
        path = record.path
        if record.origin == "inline" and classify_path(path) is None:
            path = f"{path}.md"
        return chunk_file(path, text, self.chunking)

    def _apply_file(
        self,
        corpus: CorpusRecord,
        record: FileRecord,
        text: str,
        gens: dict[str, int],
        stats: _Stats,
        previous: FileRecord | None,
        *,
        revalidate: bool,
    ) -> None:
        chunked = self._chunk(record, text)
        source_type = record.source_type
        gen = gens["code" if source_type == "code" else "doc"]
        existing = self.store.chunks_for_path(corpus.corpus_id, record.path)
        now = time.time()
        changed = False
        placed: list[tuple[str, int, int, str | None]] = []
        with self.store.transaction() as conn:
            for draft in chunked.chunks:
                chunk_id = chunk_id_for(corpus.corpus_id, source_type, record.path, draft.anchor)
                content_hash = sha256_hex(draft.content)
                fts = self._fts_fields(record.path, draft.content, draft.symbol, draft.section)
                old = existing.pop(chunk_id, None)
                new = ChunkRecord(
                    id=old.id if old else 0,
                    chunk_id=chunk_id,
                    corpus_id=corpus.corpus_id,
                    path=record.path,
                    source_type=source_type,
                    anchor=draft.anchor,
                    symbol=draft.symbol,
                    symbol_kind=draft.symbol_kind,
                    section=draft.section,
                    language=draft.language,
                    start_line=draft.start_line,
                    end_line=draft.end_line,
                    content=draft.content,
                    content_hash=content_hash,
                    file_hash=record.file_hash,
                    version=record.version,
                    snapshot_id=None,
                    token_count=estimate_tokens(draft.content),
                    status=ACTIVE,
                    status_reason=None,
                    generation=gen,
                    indexed_at=old.indexed_at if old else now,
                    updated_at=now,
                )
                placed.append((chunk_id, draft.start_line, draft.end_line, draft.symbol))
                stats.embed_hashes[content_hash] = draft.content
                keep = (
                    old is not None
                    and old.content_hash == content_hash
                    and (old.status == ACTIVE or (old.status == INVALIDATED and not revalidate))
                )
                if old is not None and keep:
                    # Metadata-only refresh (line numbers, file hash, version): no change record.
                    # A manually invalidated chunk with unchanged content stays invalidated.
                    new.status, new.status_reason = old.status, old.status_reason
                    new.generation = old.generation
                    self.store.replace_chunk(conn, new, None)
                    stats.chunks["unchanged"] += 1
                    continue
                if old is not None:
                    change = "revalidated" if old.content_hash == content_hash else "modified"
                    self.store.replace_chunk(conn, new, fts)
                else:
                    change = "added"
                    self.store.insert_chunk(conn, new, fts)
                self.store.record_change(
                    conn, corpus.corpus_id, source_type, gen, chunk_id, record.path, change
                )
                stats.chunks[change] += 1
                changed = True
            for old in existing.values():
                if old.status == DELETED:
                    continue
                self.store.deactivate_chunk(conn, old, DELETED, "removed_from_file", gen)
                self.store.record_change(
                    conn, corpus.corpus_id, source_type, gen, old.chunk_id, record.path, "deleted"
                )
                stats.chunks["deleted"] += 1
                changed = True
            self.store.replace_symbols(
                conn, corpus.corpus_id, record.path, self._symbol_rows(chunked.symbols, placed)
            )
            self.store.upsert_file(conn, record)
        if changed:
            stats.changed_types.add(source_type)
        if previous is None:
            stats.files["added"] += 1
        elif previous.status == ACTIVE:
            stats.files["updated"] += 1
        else:
            stats.files["revalidated"] += 1

    def _delete_file(
        self,
        corpus: CorpusRecord,
        record: FileRecord,
        reason: str,
        gens: dict[str, int],
        stats: _Stats,
    ) -> None:
        gen = gens["code" if record.source_type == "code" else "doc"]
        existing = self.store.chunks_for_path(corpus.corpus_id, record.path)
        with self.store.transaction() as conn:
            for old in existing.values():
                if old.status == DELETED:
                    continue
                self.store.deactivate_chunk(conn, old, DELETED, reason, gen)
                self.store.record_change(
                    conn,
                    corpus.corpus_id,
                    record.source_type,
                    gen,
                    old.chunk_id,
                    record.path,
                    "deleted",
                )
                stats.chunks["deleted"] += 1
            self.store.replace_symbols(conn, corpus.corpus_id, record.path, [])
            record.status = DELETED
            record.status_reason = reason
            self.store.upsert_file(conn, record)
        stats.files["deleted"] += 1
        stats.changed_types.add(record.source_type)

    @staticmethod
    def _symbol_rows(
        symbols: list[SymbolDef], placed: list[tuple[str, int, int, str | None]]
    ) -> list[tuple[str, str, str, int, int, str | None]]:
        rows = []
        for symbol in symbols:
            chunk_id = next(
                (
                    c
                    for c, s, e, q in placed
                    if q == symbol.qualname and s <= symbol.start_line <= e
                ),
                None,
            )
            if chunk_id is None:
                chunk_id = next(
                    (c for c, s, e, q in placed if q is not None and q.startswith(symbol.qualname)),
                    None,
                )
            if chunk_id is None:
                chunk_id = next((c for c, s, e, _ in placed if s <= symbol.start_line <= e), None)
            rows.append(
                (
                    symbol.name,
                    symbol.qualname,
                    symbol.kind,
                    symbol.start_line,
                    symbol.end_line,
                    chunk_id,
                )
            )
        return rows

    @staticmethod
    def _fts_fields(
        path: str, content: str, symbol: str | None, section: str | None
    ) -> tuple[str, str, str]:
        body = expand_for_index(f"{section}\n{content}" if section else content)
        symbol_text = ""
        if symbol:
            base = symbol.split("#")[0]
            symbol_text = f"{base} {' '.join(base.split('.'))} {' '.join(split_identifier(base))}"
        path_text = f"{path} {' '.join(split_identifier(path))}"
        return body, symbol_text, path_text

    def _finish(self, corpus: CorpusRecord, root: Path | None, stats: _Stats) -> None:
        self._embed(corpus.corpus_id, stats)
        fields: dict[str, Any] = {"last_sync_at": time.time()}
        if "code" in stats.changed_types:
            fields["code_generation"] = corpus.code_generation + 1
        if stats.changed_types - {"code"}:
            fields["docs_generation"] = corpus.docs_generation + 1
        files = self.store.files(corpus.corpus_id)
        code_snapshot = "tree:" + short_hash(
            *sorted(
                f"{p}:{f.file_hash}"
                for p, f in files.items()
                if f.source_type == "code" and f.status == ACTIVE
            ),
            length=16,
        )
        docs_snapshot = "docs:" + short_hash(
            *sorted(
                f"{p}:{f.version}"
                for p, f in files.items()
                if f.source_type == "doc" and f.status == ACTIVE
            ),
            length=16,
        )
        fields["code_snapshot"] = code_snapshot
        fields["docs_snapshot"] = docs_snapshot
        if root is not None:
            fields["git_head"] = read_git_head(root)
        self.store.update_corpus_state(corpus.corpus_id, **fields)
        with self.store.transaction() as conn:
            # Stamp the snapshot on chunks that were (re)confirmed in this sync.
            conn.execute(
                "UPDATE chunks SET snapshot_id = CASE source_type WHEN 'code' THEN ? ELSE ? END "
                "WHERE corpus_id = ? AND status = 'active' AND updated_at >= ?",
                (code_snapshot, docs_snapshot, corpus.corpus_id, corpus.last_sync_at or 0),
            )
            config = dict(corpus.config)
            config["chunker_version"] = CHUNKER_VERSION
            conn.execute(
                "UPDATE corpora SET config_json = ? WHERE corpus_id = ?",
                (json.dumps(config, sort_keys=True), corpus.corpus_id),
            )

    def _embed(self, corpus_id: str, stats: _Stats) -> None:
        """Embed new content plus a bounded backlog of active chunks still lacking vectors
        (e.g. after an embedder outage or an interrupted run), with a persisted back-off so an
        unavailable embedder does not stall every call."""
        model_id = self.embedder.model_id
        backoff_key = f"embed_backoff_until:{model_id}"
        hashes = dict(stats.embed_hashes)
        if time.time() < float(self.store.get_meta(backoff_key) or 0.0):
            pending = len(hashes) - len(self.store.get_embeddings(model_id, hashes.keys()))
            if pending:
                stats.embeddings["pending"] += pending
                stats.degraded.append("embedding_unavailable")
            return
        backlog = {
            h: content
            for h, content in self.store.chunks_missing_embeddings(
                model_id, corpus_id, EMBED_BACKFILL_LIMIT
            )
            if h not in hashes
        }
        hashes.update(backlog)
        if not hashes:
            return
        existing = self.store.get_embeddings(model_id, hashes.keys())
        missing = [h for h in hashes if h not in existing]
        stats.embeddings["reused"] += len(hashes) - len(missing)
        if not missing:
            return
        try:
            for offset in range(0, len(missing), 256):
                batch = missing[offset : offset + 256]
                vectors = self.embedder.embed_documents([hashes[h] for h in batch])
                self.store.put_embeddings(model_id, {h: vectors[i] for i, h in enumerate(batch)})
                stats.embeddings["computed"] += len(batch)
        except EmbeddingError:
            stats.embeddings["pending"] += len(missing) - stats.embeddings["computed"]
            stats.degraded.append("embedding_unavailable")
            self.store.set_meta(backoff_key, str(time.time() + EMBED_BACKOFF_SECONDS))
        # Vectors for rows that did not change (back-fill) are not in the change log.
        if stats.embeddings["computed"] and (backlog or not stats.changed_types):
            self.vector_index.invalidate(corpus_id)

    def _mark_corpus_stale(self, corpus_id: str) -> None:
        """Drop a corpus's file records and mark it stale after its root has disappeared."""
        self.store.delete_all_files(corpus_id)
        self.store.mark_stale(corpus_id, True)

    def _response(
        self,
        corpus_id: str,
        stats: _Stats,
        started: float,
        stale: bool = False,
    ) -> IndexResponse:
        corpus = self.store.get_corpus(corpus_id)
        assert corpus is not None
        return IndexResponse(
            corpus_id=corpus_id,
            code_generation=corpus.code_generation,
            docs_generation=corpus.docs_generation,
            code_snapshot=corpus.code_snapshot,
            docs_snapshot=corpus.docs_snapshot,
            git_head=corpus.git_head,
            stale=stale,
            files=stats.files,
            chunks=stats.chunks,
            embeddings=stats.embeddings,
            degraded_reasons=sorted(set(stats.degraded)),
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
