"""Retrieval result cache.

Key dimensions
--------------
* **scope** — retrieval profile, effective ``config_id``, corpora and the task context
  (``mission_id`` + ``task_id``); reuse never crosses tasks or profiles;
* **state** — per-corpus code and docs generations for the source types the profile reads;
* **query** — the component-wise query fingerprint (fingerprint.py).

Lookup order: exact key → equivalent fingerprint at the same state (``semantic_hit``) →
equivalent fingerprint at an older state whose delta provably cannot affect the result
(``revalidated_hit``). The cache stores chunk ids, content hashes and scores, never rendered
text: hits are re-hydrated from the store and re-checked by the deterministic filters.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .fingerprint import QueryFingerprint, compare
from .profiles import Profile
from .query_builder import BuiltQuery
from .store import ACTIVE, Store
from .text import sha256_hex

# Scores changed chunks against the current query: {chunk_id: {"lexical": x, "vector": y}}.
DeltaScorer = Callable[[list[str]], dict[str, dict[str, float]]]


@dataclass
class CacheLookup:
    status: str  # hit | semantic_hit | revalidated_hit | miss
    cache_key: str
    result: dict[str, Any] | None = None
    reason: str | None = None


def scope_key(
    profile: Profile, corpora: list[str], mission_id: str | None, task_id: str | None
) -> str:
    task_scope = f"{mission_id or '-'}::{task_id or '-'}"
    return (
        "scope_"
        + sha256_hex(json.dumps([profile.name, profile.config_id(), sorted(corpora), task_scope]))[
            :24
        ]
    )


def cache_key(scope: str, state: dict[str, Any], fingerprint: QueryFingerprint) -> str:
    return (
        "rc_" + sha256_hex(json.dumps([scope, state, fingerprint.exact_hash], sort_keys=True))[:32]
    )


class RetrievalCache:
    def __init__(self, store: Store, ttl_seconds: float, max_entries: int) -> None:
        self.store = store
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._writes = 0

    def lookup(
        self,
        scope: str,
        state: dict[str, Any],
        query: BuiltQuery,
        profile: Profile,
        delta_scorer: DeltaScorer,
    ) -> CacheLookup:
        key = cache_key(scope, state, query.fingerprint)
        now = time.time()
        row = self.store.cache_get(key)
        if row is not None and now - row["created_at"] <= self.ttl_seconds:
            return CacheLookup("hit", key, json.loads(row["result_json"]))
        miss_reason = "no_entry"
        for entry in self.store.cache_scope_entries(scope):
            if now - entry["created_at"] > self.ttl_seconds:
                continue
            cached_fp = QueryFingerprint.from_dict(json.loads(entry["fingerprint_json"]))
            equivalence = compare(query.fingerprint, cached_fp, profile.cache.jaccard_threshold)
            if not equivalence.equivalent:
                miss_reason = f"query_changed:{equivalence.detail.get('component', '?')}"
                continue
            if equivalence.level == "semantic" and not profile.cache.semantic_reuse:
                miss_reason = "semantic_reuse_disabled"
                continue
            result = json.loads(entry["result_json"])
            cached_state = json.loads(entry["state_json"])
            if cached_state == state:
                status = "hit" if equivalence.level == "exact" else "semantic_hit"
                return CacheLookup(status, entry["cache_key"], result, equivalence.level)
            ok, why = self._revalidate(cached_state, state, result, query, profile, delta_scorer)
            if ok:
                return CacheLookup("revalidated_hit", entry["cache_key"], result, equivalence.level)
            miss_reason = why
        return CacheLookup("miss", key, None, miss_reason)

    def _revalidate(
        self,
        cached_state: dict[str, Any],
        state: dict[str, Any],
        result: dict[str, Any],
        query: BuiltQuery,
        profile: Profile,
        delta_scorer: DeltaScorer,
    ) -> tuple[bool, str]:
        if set(cached_state) != set(state):
            return False, "corpora_changed"
        delta = []
        for corpus_id, gens in state.items():
            old = cached_state[corpus_id]
            for source_type, generation in gens.items():
                if generation < old.get(source_type, 0):
                    return False, "generation_regressed"
                if generation != old.get(source_type):
                    delta.extend(
                        self.store.changes_since(
                            0,
                            corpus_id=corpus_id,
                            source_type=source_type,
                            min_generation=old.get(source_type, 0),
                        )
                    )
        if len(delta) > profile.cache.max_delta_chunks:
            return False, "delta_too_large"
        selected = result.get("selected", [])
        current = self.store.get_chunks([s["chunk_id"] for s in selected])
        for item in selected:
            chunk = current.get(item["chunk_id"])
            if (
                chunk is None
                or chunk.status != ACTIVE
                or chunk.content_hash != item["content_hash"]
            ):
                return False, "selected_chunk_changed"
        selected_paths = {(s["corpus_id"], s["path"]) for s in selected}
        changed_ids = [
            c.chunk_id for c in delta if c.change in {"added", "modified", "revalidated"}
        ]
        if any((c.corpus_id, c.path) in selected_paths for c in delta):
            return False, "selected_file_changed"
        anchors = {p.lower() for p in query.paths} | {i.lower() for i in query.identifiers}
        changed = self.store.get_chunks(changed_ids)
        for chunk in changed.values():
            name = (chunk.symbol or "").split("#")[0].split(".")[-1].lower()
            if (name and name in anchors) or any(
                chunk.path.lower().endswith(a) for a in anchors if "/" in a or "." in a
            ):
                return False, "delta_matches_anchor"
        if changed_ids:
            floors: dict[str, float] = {}
            for kind in ("lexical", "vector"):
                values = [s[kind] for s in selected if s.get(kind) is not None]
                if values:
                    floors[kind] = min(values)
            floors.setdefault("lexical", 0.0)
            for scores in delta_scorer(changed_ids).values():
                for kind, value in scores.items():
                    if kind in floors and value >= floors[kind]:
                        return False, f"delta_{kind}_relevant"
        return True, "revalidated"

    def put(
        self,
        scope: str,
        state: dict[str, Any],
        query: BuiltQuery,
        result: dict[str, Any],
    ) -> str:
        key = cache_key(scope, state, query.fingerprint)
        self.store.cache_put(key, scope, state, query.fingerprint.to_dict(), result)
        self._writes += 1
        if self._writes % 100 == 0:
            self.store.cache_prune(self.ttl_seconds, self.max_entries)
        return key
