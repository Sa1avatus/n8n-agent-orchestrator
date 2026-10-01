import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from ahawr_retrieval.candidates import Candidate
from ahawr_retrieval.chunking import chunk_file
from ahawr_retrieval.config import Settings
from ahawr_retrieval.context import chunk_cost, related_test_paths, render_context, select_context
from ahawr_retrieval.models import IndexDocument, IndexRequest, RetrieveRequest
from ahawr_retrieval.profiles import BudgetConfig, Profile
from ahawr_retrieval.query_builder import BuiltQuery, build_query
from ahawr_retrieval.ranking import rank_candidates
from ahawr_retrieval.reranker import HttpReranker
from ahawr_retrieval.service import RetrievalError, RetrievalService
from ahawr_retrieval.store import ChunkRecord

from .conftest import make_service

TASK = {
    "mission_id": "m1",
    "task_id": "T001",
    "title": "Fix invoice totals",
    "objective": "compute_total must not count tax lines twice when summing invoice totals",
    "acceptance_criteria": ["tests/test_parser.py passes"],
    "verification": ["pytest tests/test_parser.py"],
    "scope": ["app/parser.py"],
}


def retrieve(service: RetrievalService, **overrides: Any) -> Any:
    payload: dict[str, Any] = {"profile": "worker", "corpora": ["ws"], "task": TASK}
    payload.update(overrides)
    return service.retrieve(RetrieveRequest(**payload))


def test_worker_retrieval_returns_provenance(indexed: RetrievalService) -> None:
    response = retrieve(indexed)
    assert response.chunks
    top = response.chunks[0]
    assert top.path == "app/parser.py" and top.symbol == "compute_total"
    assert top.rank == 1 and top.source_type == "code" and top.authority == "current_code"
    assert top.content_hash.startswith("sha256:") and top.file_hash.startswith("sha256:")
    assert top.snapshot_id and top.snapshot_id.startswith("tree:")
    assert top.freshness == "verified"
    assert top.scores.lexical is not None and top.scores.fused is not None
    assert top.features["exact_symbol"] == 1.0 and top.features["scope_match"] == 1.0
    assert "=== RETRIEVED CONTEXT profile=worker" in response.context
    assert "path=app/parser.py" in response.context and "symbol=compute_total" in response.context
    assert response.snapshots["ws"]["code_generation"] == 1
    assert "reranker_not_configured" not in response.degraded_reasons
    assert "reranker_disabled" in response.notes
    assert not response.degraded
    assert response.stats["backend"] == "local"


def test_docs_are_retrieved_with_section_provenance(indexed: RetrievalService) -> None:
    response = retrieve(indexed, query="How are payment retries bounded?", task={})
    doc = next(c for c in response.chunks if c.source_type == "doc")
    assert doc.section == "Billing Guide > Payment retries"
    assert doc.authority == "current_documentation"


def test_reviewer_profile_prefers_tests_and_changed_files(indexed: RetrievalService) -> None:
    response = retrieve(
        indexed,
        profile="reviewer",
        review={
            "worker_output": "FIX APPLIED: updated compute_total in app/parser.py",
            "changed_files": ["app/parser.py"],
        },
    )
    paths = [c.path for c in response.chunks]
    assert "tests/test_parser.py" in paths[:4]
    assert response.profile == "reviewer"


# ------------------------------------------------------------- changed_paths


# Deterministic result fields that must be identical when the request differs only in
# changed_paths (or when the field is absent). Per-call values are excluded: request_id
# and timings_ms (never compared), and context / snapshots (each embeds a per-call
# request_id and a last_sync_at timestamp that vary between two identical requests).
_RESULT_FIELDS = (
    "profile",
    "config_id",
    "query",
    "degraded",
    "degraded_reasons",
    "notes",
    "chunks",
    "context_tokens",
    "stats",
    "candidates",
)


def _result_fields(response: Any) -> dict[str, Any]:
    data = response.model_dump()
    out = {name: data[name] for name in _RESULT_FIELDS}
    # Per-chunk features legitimately vary with changed_paths (the changed_path
    # feature is exposed per chunk); compare only the ranking-relevant chunk fields.
    out["chunks"] = [{k: v for k, v in chunk.items() if k != "features"} for chunk in out["chunks"]]
    return out


def test_changed_paths_ignored_without_field_matches_old_behaviour(
    indexed: RetrievalService,
) -> None:
    # A request without changed_paths must be identical (in result fields) to one where
    # the field is absent (None): the regression criterion holds field-by-field.
    # cache="bypass" keeps both computations fresh so hydration does not mask the
    # comparison (a hit re-hydrates and re-computes stats/context).
    plain = retrieve(indexed, cache="bypass")
    explicit_none = retrieve(indexed, changed_paths=None, cache="bypass")
    assert _result_fields(plain) == _result_fields(explicit_none)
    # ... and the reviewer profile without changed_paths matches its pre-T010 output.
    review = {
        "worker_output": "FIX APPLIED: updated compute_total in app/parser.py",
        "changed_files": ["app/parser.py"],
    }
    base = retrieve(indexed, profile="reviewer", review=review, cache="bypass")
    base_none = retrieve(
        indexed, profile="reviewer", changed_paths=None, review=review, cache="bypass"
    )
    assert _result_fields(base) == _result_fields(base_none)


def test_reviewer_changed_paths_boost_and_inclusion(indexed: RetrievalService) -> None:
    # web/client.ts is a normal corpus file not mentioned by the review; boosting its
    # changed fragment lifts it into the reviewer context where it would otherwise not
    # be selected at the tight reviewer budget.
    plain = retrieve(
        indexed,
        profile="reviewer",
        review={
            "worker_output": "FIX APPLIED: updated compute_total in app/parser.py",
            "changed_files": ["app/parser.py"],
        },
    )
    boosted = retrieve(
        indexed,
        profile="reviewer",
        changed_paths=["web/client.ts"],
        review={
            "worker_output": "FIX APPLIED: updated compute_total in app/parser.py",
            "changed_files": ["app/parser.py"],
        },
    )
    boosted_paths = [c.path for c in boosted.chunks]
    assert "web/client.ts" in boosted_paths
    # The changed fragment actually scored higher than in the unboosted request.
    plain_scores = {c.path: c.scores.final for c in plain.chunks}
    boosted_scores = {c.path: c.scores.final for c in boosted.chunks}
    assert boosted_scores["web/client.ts"] > plain_scores.get("web/client.ts", 0.0)
    # The feature is visible on the changed candidate.
    changed = next(c for c in boosted.chunks if c.path == "web/client.ts")
    assert changed.features["changed_path"] == 1.0
    # The reviewer profile is unchanged in identity and budget.
    assert boosted.profile == "reviewer"
    assert boosted.config_id == plain.config_id


def test_unknown_or_out_of_corpus_changed_paths_are_ignored(indexed: RetrievalService) -> None:
    # Neither an unknown path nor a valid path outside the corpus can change the
    # result: no matching chunk exists, so the feature stays 0 everywhere and the
    # ranking is unchanged. They only enter the cache key (see the key test below).
    review = {
        "worker_output": "FIX APPLIED: updated compute_total in app/parser.py",
        "changed_files": ["app/parser.py"],
    }
    base = retrieve(indexed, profile="reviewer", review=review, cache="bypass")
    other = retrieve(
        indexed,
        profile="reviewer",
        changed_paths=["nonexistent/file.py", "other-corpus/out.py"],
        review=review,
        cache="bypass",
    )
    assert _result_fields(base) == _result_fields(other)


def test_changed_paths_enter_cache_key(indexed: RetrievalService) -> None:
    base = retrieve(
        indexed,
        profile="reviewer",
        review={
            "worker_output": "FIX APPLIED: updated compute_total in app/parser.py",
            "changed_files": ["app/parser.py"],
        },
    )
    with_changed = retrieve(
        indexed,
        profile="reviewer",
        changed_paths=["web/client.ts"],
        review={
            "worker_output": "FIX APPLIED: updated compute_total in app/parser.py",
            "changed_files": ["app/parser.py"],
        },
    )
    # Different changed_paths => a different cache key, so the second request is a
    # miss even though the rest of the request is identical.
    assert base.cache.key != with_changed.cache.key
    assert with_changed.cache.status == "miss"


def test_absent_changed_paths_keeps_preexisting_cache_key(indexed: RetrievalService) -> None:
    # A request where the field is absent (None) must produce the exact same key as
    # before T010; two consecutive identical requests still hit the same cache entry.
    first = retrieve(indexed)
    second = retrieve(indexed)
    assert first.cache.status == "miss"
    assert second.cache.status == "hit"
    assert second.cache.key == first.cache.key
    # Explicit None is treated the same as an absent field.
    retrieve(indexed, changed_paths=None)
    # A miss here is fine: it just proves the key is stable (it would hit if cached).
    import json

    from ahawr_retrieval.cache import cache_key
    from ahawr_retrieval.models import RetrieveRequest
    from ahawr_retrieval.profiles import default_profiles
    from ahawr_retrieval.query_builder import build_query
    from ahawr_retrieval.text import sha256_hex

    default_profiles()["worker"]
    query = build_query(RetrieveRequest(corpora=["ws"], task=TASK))
    k_none = cache_key("scope_x", {}, query.fingerprint, None)
    # The pre-T010 key shape omits the changed_paths dimension entirely; the absent
    # (None) request reproduces it byte-for-byte.
    k_old_shape = (
        "rc_"
        + sha256_hex(json.dumps(["scope_x", {}, query.fingerprint.exact_hash], sort_keys=True))[:32]
    )
    assert k_none == k_old_shape
    # An explicitly empty list is a distinct (trivial) dimension and is not the absent key.
    k_empty = cache_key("scope_x", {}, query.fingerprint, [])
    assert k_empty != k_none


def test_existing_response_fields_unchanged(indexed: RetrievalService) -> None:
    response = retrieve(
        indexed,
        profile="reviewer",
        changed_paths=["app/parser.py", "web/client.ts"],
        review={
            "worker_output": "FIX APPLIED: updated compute_total in app/parser.py",
            "changed_files": ["app/parser.py"],
        },
    )
    # Every top-level response field is still present and unchanged in shape.
    assert set(response.model_dump()) == {
        "request_id",
        "profile",
        "config_id",
        "query",
        "cache",
        "snapshots",
        "degraded",
        "degraded_reasons",
        "notes",
        "chunks",
        "context",
        "context_tokens",
        "stats",
        "timings_ms",
        "candidates",
    }
    # The changed_paths feature is exposed on each chunk alongside existing features.
    for chunk in response.chunks:
        assert "changed_path" in chunk.features


# ------------------------------------------------------- automatic reviewer boost


def _reviewer_request(service: RetrievalService, **overrides: Any) -> Any:
    payload: dict[str, Any] = {
        "profile": "reviewer",
        "corpora": ["ws"],
        "task": TASK,
        "review": {
            "worker_output": "FIX APPLIED: updated compute_total in app/parser.py",
            "changed_files": ["app/parser.py"],
        },
    }
    payload.update(overrides)
    return service.retrieve(RetrieveRequest(**payload))


def test_reviewer_boosts_files_changed_since_last_worker_request(
    indexed: RetrievalService, workspace: Path
) -> None:
    # Worker request logged for TASK; then app/parser.py is modified and re-synced
    # (the sync/index journal records it via files.indexed_at); a reviewer request for
    # the same task — with no explicit changed_paths — must get the changed file
    # boosted exactly as if it had been passed in as changed_paths.
    retrieve(indexed, trace={"mission_id": "m1", "task_id": "T001"})
    target = "web/client.ts"  # a corpus file the review text does not mention
    (workspace / target).write_text("CHANGED BY WORKER\n" * 4, encoding="utf-8")
    time.sleep(0.05)
    indexed.index(IndexRequest(corpus_id="ws", root=str(workspace)))

    # The plain request uses a different task (different title/objective) so the fallback
    # key does not match the worker request; it is the unboosted baseline.
    plain = _reviewer_request(
        indexed,
        cache="bypass",
        task={
            **{k: v for k, v in TASK.items() if k not in ("mission_id", "task_id")},
            "title": "Unrelated review task",
            "objective": "no shared objective",
        },
    )
    boosted = _reviewer_request(indexed, trace={"mission_id": "m1", "task_id": "T001"})
    boosted_paths = [c.path for c in boosted.chunks]
    assert target in boosted_paths, "changed file was not boosted into the reviewer context"
    assert boosted.cache.status == "miss"
    # The changed fragment actually scored higher than in the unboosted request.
    plain_scores = {c.path: c.scores.final for c in plain.chunks}
    boosted_scores = {c.path: c.scores.final for c in boosted.chunks}
    assert boosted_scores[target] > plain_scores.get(target, 0.0)
    # The boost was carried through as changed_paths (same feature as the explicit field).
    changed = next(c for c in boosted.chunks if c.path == target)
    assert changed.features["changed_path"] == 1.0
    # The journal query ran (timing is recorded).
    assert "changed_paths" in boosted.timings_ms
    assert boosted.timings_ms["changed_paths"] >= 0


def test_reviewer_boost_is_not_applied_to_a_different_task(
    indexed: RetrievalService, workspace: Path
) -> None:
    # Worker request for TASK is logged; a file is modified and re-synced; a reviewer
    # request for a *different* task must not be boosted (no correlation match).
    retrieve(indexed, trace={"mission_id": "m1", "task_id": "T001"})
    target = "web/client.ts"
    (workspace / target).write_text("CHANGED BY WORKER\n" * 4, encoding="utf-8")
    time.sleep(0.05)
    indexed.index(IndexRequest(corpus_id="ws", root=str(workspace)))

    other_task = dict(TASK, mission_id="m1", task_id="T002", title="Unrelated task")
    plain = _reviewer_request(indexed, task=other_task, cache="bypass")
    boosted = _reviewer_request(
        indexed, task=other_task, trace={"mission_id": "m1", "task_id": "T002"}
    )
    # Without a matching worker request there is nothing to boost: the two are identical.
    assert _result_fields(plain) == _result_fields(boosted)
    # No changed file from the journal is boosted (the feature is 0 everywhere).
    for chunk in boosted.chunks:
        assert chunk.features["changed_path"] == 0.0


def test_reviewer_boost_merges_explicit_changed_paths_with_journal(
    indexed: RetrievalService, workspace: Path
) -> None:
    # An explicit changed_paths is merged with the files the journal reports as changed
    # since the last worker request; both are boosted.
    retrieve(indexed, trace={"mission_id": "m1", "task_id": "T001"})
    target = "web/client.ts"
    (workspace / target).write_text("CHANGED BY WORKER\n" * 4, encoding="utf-8")
    time.sleep(0.05)
    indexed.index(IndexRequest(corpus_id="ws", root=str(workspace)))

    explicit = _reviewer_request(
        indexed, changed_paths=["app/parser.py"], trace={"mission_id": "m1", "task_id": "T001"}
    )
    explicit_paths = [c.path for c in explicit.chunks]
    # app/parser.py (explicit) and web/client.ts (journal) are both boosted.
    assert "app/parser.py" in explicit_paths
    assert target in explicit_paths
    for chunk in explicit.chunks:
        if chunk.path in ("app/parser.py", target):
            assert chunk.features["changed_path"] == 1.0
    # The journal (files_indexed_since) reports exactly the file re-indexed since the
    # worker request; the merged set is the union of the explicit paths and the journal.
    worker_ts = [r for r in indexed.log.requests() if r["profile"] == "worker"][-1]["ts"]
    journal = {p for _, p in indexed.store.files_indexed_since(worker_ts, ["ws"])}
    assert target in journal
    # The merged changed_paths the cache key is built from equals explicit ∪ journal.
    from ahawr_retrieval.cache import cache_key, scope_key
    from ahawr_retrieval.models import RetrieveRequest
    from ahawr_retrieval.profiles import default_profiles
    from ahawr_retrieval.query_builder import build_query

    profile = default_profiles()["reviewer"]
    query = build_query(
        RetrieveRequest(
            profile="reviewer",
            corpora=["ws"],
            task=TASK,
            review={
                "worker_output": "FIX APPLIED: updated compute_total in app/parser.py",
                "changed_files": ["app/parser.py"],
            },
            changed_paths=["app/parser.py"],
        )
    )
    scope = scope_key(profile, ["ws"], "m1", "T001")
    # state as the service builds it: code/docs generations for the reviewer profile.
    state = {
        "ws": {
            "code": indexed.store.get_corpus("ws").code_generation,
            "doc": indexed.store.get_corpus("ws").docs_generation,
        }
    }
    key_merged = cache_key(scope, state, query.fingerprint, sorted({"app/parser.py", target}))
    key_only_explicit = cache_key(scope, state, query.fingerprint, ["app/parser.py"])
    # The explicit request's key must be the merged one, not a key built from the
    # explicit paths alone (the journal contributes an extra path).
    assert explicit.cache.key == key_merged, (
        "explicit changed_paths must be merged with the journal in the cache key"
    )
    assert explicit.cache.key != key_only_explicit


def test_reviewer_boost_uses_the_fallback_task_component_key(
    indexed: RetrievalService, workspace: Path
) -> None:
    # When no explicit mission/task id is provided, the boost falls back to the hash of
    # the reviewer's "task" query component, matching the same task across worker and
    # reviewer requests even without an explicit key. The reviewer request carries a
    # trace (so the boost runs); without the task component it would not match.
    retrieve(indexed)  # worker request, no trace -> no explicit key
    target = "web/client.ts"
    (workspace / target).write_text("CHANGED BY WORKER\n" * 4, encoding="utf-8")
    time.sleep(0.05)
    indexed.index(IndexRequest(corpus_id="ws", root=str(workspace)))

    _reviewer_request(
        indexed,
        cache="bypass",
        task={k: v for k, v in TASK.items() if k not in ("mission_id", "task_id")},
    )
    boosted = _reviewer_request(
        indexed,
        trace={"role": "reviewer"},
        task={k: v for k, v in TASK.items() if k not in ("mission_id", "task_id")},
    )  # no mission/task ids -> fallback component key
    boosted_paths = [c.path for c in boosted.chunks]
    assert target in boosted_paths, "fallback key did not match the worker request"
    changed = next(c for c in boosted.chunks if c.path == target)
    assert changed.features["changed_path"] == 1.0
    # The lookup is recorded as a timed, indexed step.
    assert "changed_paths" in boosted.timings_ms


def test_reviewer_boost_correlates_task_ids_across_task_and_trace(
    indexed: RetrievalService, workspace: Path
) -> None:
    # The correlation keys are read from the task, falling back to the trace. Here the
    # Worker puts mission_id/task_id only in its task (no trace); the Reviewer puts the
    # same ids only in its trace (no ids in its task). The id lookup still matches, so
    # the changed file is boosted.
    task_ids = dict(TASK)
    retrieve(indexed, task=task_ids)  # Worker: ids in task, no trace
    target = "web/client.ts"
    (workspace / target).write_text("CHANGED BY WORKER\n" * 4, encoding="utf-8")
    time.sleep(0.05)
    indexed.index(IndexRequest(corpus_id="ws", root=str(workspace)))

    # Reviewer: same ids, but carried in the trace instead of the task.
    task_without_ids = {k: v for k, v in TASK.items() if k not in ("mission_id", "task_id")}
    boosted = _reviewer_request(
        indexed,
        task=task_without_ids,
        trace={"mission_id": "m1", "task_id": "T001"},
    )
    boosted_paths = [c.path for c in boosted.chunks]
    assert target in boosted_paths, "ids in the trace must still correlate with the worker"
    changed = next(c for c in boosted.chunks if c.path == target)
    assert changed.features["changed_path"] == 1.0
    assert "changed_paths" in boosted.timings_ms


def test_reviewer_boost_lookups_are_indexed_and_bounded(
    indexed: RetrievalService, workspace: Path
) -> None:
    # The lookups (last_request / last_worker_request) and the journal query
    # (files_indexed_since) are all indexed; on a log with many worker requests the
    # extra work of the boost stays negligible — timing only the boost lookup itself
    # (not the full retrieval) proves the overhead is bounded.
    for _ in range(50):
        retrieve(indexed, trace={"mission_id": "m1", "task_id": "T001"})
    target = "web/client.ts"
    (workspace / target).write_text("CHANGED BY WORKER\n" * 4, encoding="utf-8")
    time.sleep(0.05)
    indexed.index(IndexRequest(corpus_id="ws", root=str(workspace)))

    request = RetrieveRequest(
        profile="reviewer",
        corpora=["ws"],
        task=TASK,
        review={
            "worker_output": "FIX APPLIED: updated compute_total in app/parser.py",
            "changed_files": ["app/parser.py"],
        },
        trace={"mission_id": "m1", "task_id": "T001"},
        cache="bypass",
    )
    query = build_query(request)
    start = time.perf_counter()
    indexed._reviewer_changed_paths(request, query, request.corpora, time.time())
    elapsed_ms = (time.perf_counter() - start) * 1000
    # The indexed lookup + journal query stay well under 10 ms even with 50 prior
    # worker rows in the log.
    assert elapsed_ms < 10.0, f"reviewer boost lookup took {elapsed_ms:.2f} ms"


def test_reviewer_boost_is_indexed_point_query_not_scan(
    indexed: RetrievalService, workspace: Path
) -> None:
    # Verify the added lookups are served by indexes (no table scan).
    # last_request uses retrieval_requests_task; last_worker_request uses
    # retrieval_requests_component; files_indexed_since uses files_corpus_indexed.
    plan = indexed.log._conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM retrieval_requests "
        "WHERE trace_mission_id = ? AND trace_task_id = ? AND profile = ? AND ts < ? "
        "ORDER BY ts DESC LIMIT 1",
        ("m1", "T001", "worker", 0.0),
    )
    assert any("retrieval_requests_task" in r[3] for r in plan), "last_request is not indexed"
    plan = indexed.log._conn.execute(
        "EXPLAIN QUERY PLAN SELECT * FROM retrieval_requests "
        "WHERE profile = 'worker' AND query_component = ? AND component_value = ? "
        "AND ts < ? ORDER BY ts DESC LIMIT 1",
        ("task", "x", 0.0),
    )
    assert any("retrieval_requests_component" in r[3] for r in plan), (
        "last_worker_request is not indexed"
    )
    plan = indexed.store._conn.execute(
        "EXPLAIN QUERY PLAN SELECT corpus_id, path FROM files "
        "WHERE corpus_id IN (?) AND indexed_at > ? ORDER BY corpus_id, path",
        ("ws", 0.0),
    )
    assert any("files_corpus_indexed" in r[3] for r in plan), "files_indexed_since is not indexed"


def test_worker_profile_ignores_changed_paths_by_default(indexed: RetrievalService) -> None:
    # The worker profile does not set a changed_path_bonus; providing changed_paths
    # must therefore leave the worker output identical (in result fields) to the
    # field-less request.
    plain = retrieve(indexed, cache="bypass")
    with_changed = retrieve(
        indexed, changed_paths=["app/parser.py", "web/client.ts"], cache="bypass"
    )
    assert _result_fields(plain) == _result_fields(with_changed)


def test_changed_paths_are_trimmed_and_deduplicated(indexed: RetrievalService) -> None:
    # Whitespace and duplicates in the input list do not change the result.
    base = retrieve(indexed, changed_paths=["app/parser.py"])
    same = retrieve(
        indexed, changed_paths=["  app/parser.py  ", "app/parser.py", "", "app/parser.py"]
    )
    # The keys are identical because only the trimmed unique set participates.
    assert base.cache.key == same.cache.key


def test_changed_paths_do_not_change_response_field_names(indexed: RetrievalService) -> None:
    response = retrieve(indexed, changed_paths=["app/parser.py"])
    # No new top-level field was added to the response; changed_paths is not echoed.
    assert "changed_paths" not in response.model_dump()
    assert set(response.model_dump()) == {
        "request_id",
        "profile",
        "config_id",
        "query",
        "cache",
        "snapshots",
        "degraded",
        "degraded_reasons",
        "notes",
        "chunks",
        "context",
        "context_tokens",
        "stats",
        "timings_ms",
        "candidates",
    }


def test_budget_limits_are_enforced(indexed: RetrievalService) -> None:
    response = retrieve(indexed, budget={"max_chunks": 2, "max_tokens": 5000})
    assert len(response.chunks) <= 2
    assert response.context_tokens <= 5000
    per_path: dict[str, int] = {}
    for chunk in retrieve(indexed).chunks:
        per_path[chunk.path] = per_path.get(chunk.path, 0) + 1
    assert max(per_path.values()) <= BudgetConfig().per_path_limit


def _chunk_candidate(n: int, path: str) -> Candidate:
    record = ChunkRecord(
        id=n,
        chunk_id=f"ch{n}",
        corpus_id="ws",
        path=path,
        source_type="code",
        anchor=f"a{n}",
        symbol=f"f{n}",
        symbol_kind="function",
        section=None,
        language="python",
        start_line=n * 10 + 1,
        end_line=n * 10 + 5,
        content=f"def f{n}(): ...",
        content_hash=f"h{n}",
        file_hash="fh",
        version="v",
        snapshot_id=None,
        token_count=50,
        status="active",
        status_reason=None,
        generation=1,
        indexed_at=0.0,
        updated_at=0.0,
    )
    return Candidate(chunk_id=f"ch{n}", record=record, final=1.0)


def _patch_candidate(n: int, path: str, start_line: int, lines: int = 1100) -> Candidate:
    record = ChunkRecord(
        id=n,
        chunk_id=f"ch{n}",
        corpus_id="ws",
        path=path,
        source_type="code",
        anchor=f"a{n}",
        symbol=None,
        symbol_kind=None,
        section=None,
        language="diff",
        start_line=start_line,
        end_line=start_line + lines - 1,
        content="diff --git a/x b/x",
        content_hash=f"h{n}",
        file_hash="fh",
        version="v",
        snapshot_id=None,
        token_count=400,
        status="active",
        status_reason=None,
        generation=1,
        indexed_at=0.0,
        updated_at=0.0,
    )
    return Candidate(chunk_id=f"ch{n}", record=record, final=1.0)


def _rank(
    candidates: list[Candidate],
    paths: list[str],
    scope: list[str],
    file_line_counts: dict[tuple[str, str], int] | None = None,
) -> list[Candidate]:
    for c in candidates:
        c.fused = 0.5
        c.fused_norm = 0.5
        # Path mention is the feature under test; keep the other deterministic
        # features equal so only large-patch demotion differs between fragments.
        c.features = {}
        c.file_line_count = None
    return rank_candidates(
        candidates,
        BuiltQuery(
            lexical_text="x",
            vector_text="x",
            rerank_text="x",
            identifiers=[],
            paths=paths,
            scope=scope,
        ),
        Profile(name="worker"),
        False,
        file_line_counts=file_line_counts,
    )


def test_large_patch_ranks_below_equal_source_fragment_when_not_mentioned() -> None:
    source = _chunk_candidate(1, "app/api/main.py")
    patch = _patch_candidate(2, "patches/big.patch", start_line=1)
    # Whole-file line count drives the demotion, not the fragment span.
    ranked = _rank(
        [source, patch],
        [],
        [],
        file_line_counts={("ws", "patches/big.patch"): 1100},
    )
    assert ranked[0] is source and ranked[1] is patch
    assert source.final > patch.final
    assert patch.features["large_patch"] == 1.0
    assert source.features["large_patch"] == 0.0


def test_large_patch_is_not_demoted_when_its_path_is_named() -> None:
    patch = _patch_candidate(2, "patches/big.patch", start_line=1)
    # Mentioned path: the demotion feature must be off, and the score must match
    # an otherwise-equal, non-mentioned fragment.
    control = _patch_candidate(3, "patches/other.patch", start_line=1)
    counts = {("ws", "patches/big.patch"): 1100, ("ws", "patches/other.patch"): 1100}
    _rank([control, patch], ["patches/big.patch"], [], file_line_counts=counts)
    assert patch.final > control.final  # mentioned: no demotion; control is demoted
    assert patch.features["large_patch"] == 0.0
    assert control.features["large_patch"] == 1.0
    assert patch.final > control.final + 0.1  # the full 0.8 penalty is visible

    # The basename alone is enough; the full path need not appear.
    deep = _patch_candidate(3, "deep/dir/big.patch", start_line=1)
    by_name = _rank(
        [control, deep],
        ["big.patch"],
        [],
        file_line_counts={("ws", "deep/dir/big.patch"): 1100},
    )
    assert by_name[0].record is not None
    assert by_name[0].record.path == "deep/dir/big.patch"
    assert by_name[0].features["large_patch"] == 0.0


def test_small_patch_below_threshold_is_not_demoted() -> None:
    small = _patch_candidate(2, "patches/small.patch", start_line=1, lines=900)
    control = _patch_candidate(3, "patches/other.patch", start_line=1)
    ranked = _rank(
        [small, control],
        [],
        [],
        file_line_counts={
            ("ws", "patches/small.patch"): 900,
            ("ws", "patches/other.patch"): 1100,
        },
    )
    assert ranked[0] is small and ranked[1] is control
    assert small.features["large_patch"] == 0.0
    assert control.features["large_patch"] == 1.0
    assert small.final > control.final  # the small patch is not demoted


def test_real_long_diff_fragment_ranks_below_equal_source_when_not_mentioned() -> None:
    # A real .diff file, chunked with chunk_file into ordinary <=80-line fragments.
    # The demotion must be based on the whole file's line count, not the fragment
    # span: a single small fragment of a >1000-line patch still counts as large.
    diff_lines = [f"hunk {i} line" for i in range(1100)]
    diff_text = "\n".join(diff_lines)
    chunked = chunk_file("patches/big.patch", diff_text)
    assert len(chunked.chunks) > 1
    assert all(c.end_line - c.start_line + 1 <= 80 for c in chunked.chunks)
    whole_file_lines = max(c.end_line for c in chunked.chunks)
    assert whole_file_lines > 1000

    first_fragment = chunked.chunks[0]
    source = _chunk_candidate(1, "app/api/main.py")
    patch = Candidate(
        chunk_id="ch2",
        record=ChunkRecord(
            id=2,
            chunk_id="ch2",
            corpus_id="ws",
            path="patches/big.patch",
            source_type="code",
            anchor=first_fragment.anchor,
            symbol=None,
            symbol_kind=None,
            section=None,
            language="diff",
            start_line=first_fragment.start_line,
            end_line=first_fragment.end_line,
            content=first_fragment.content,
            content_hash=f"h{2}",
            file_hash="fh",
            version="v",
            snapshot_id=None,
            token_count=len(first_fragment.content),
            status="active",
            status_reason=None,
            generation=1,
            indexed_at=0.0,
            updated_at=0.0,
        ),
    )
    ranked = _rank(
        [source, patch],
        [],
        [],
        file_line_counts={("ws", "patches/big.patch"): whole_file_lines},
    )
    assert ranked[0] is source and ranked[1] is patch
    assert source.final > patch.final
    assert patch.features["large_patch"] == 1.0
    assert source.features["large_patch"] == 0.0


def test_per_path_limit_keeps_several_chunks_of_one_file() -> None:
    # A task centred on one large module needs more than a few of its chunks.
    ranked = [_chunk_candidate(n, "app/api/main.py") for n in range(10)]
    ranked.append(_chunk_candidate(10, "app/config.py"))
    budget = BudgetConfig()
    selected = select_context(ranked, budget, max_chunks=12, max_tokens=6000)
    main = [c for c in selected if c.record and c.record.path == "app/api/main.py"]
    assert len(main) == budget.per_path_limit == 8
    assert [c.selection_reason for c in ranked[8:10]] == ["per_path_limit"] * 2
    assert ranked[10].selected


def test_weak_chunks_are_left_out_of_the_context() -> None:
    strong, weak = _chunk_candidate(1, "app/a.py"), _chunk_candidate(2, "app/b.py")
    weak.final = 0.45
    selected = select_context([strong, weak], BudgetConfig(), max_chunks=12, max_tokens=6000)
    assert selected == [strong]
    assert weak.selection_reason == "below_min_score"
    kept = select_context(
        [strong, weak], BudgetConfig(min_final_score=0.0), max_chunks=12, max_tokens=6000
    )
    assert kept == [strong, weak]


def test_context_numbers_code_lines_with_their_file_line_numbers(
    indexed: RetrievalService, workspace: Path
) -> None:
    response = retrieve(indexed)
    source = (workspace / "app/parser.py").read_text(encoding="utf-8").splitlines()
    chunk = next(c for c in response.chunks if c.symbol == "compute_total")
    first = chunk.start_line
    assert source[first - 1].startswith("def compute_total")
    width = len(str(chunk.end_line))
    assert f"{first:>{width}}| def compute_total" in response.context
    assert response.context_tokens > sum(c.token_count for c in response.chunks)

    plain = retrieve(indexed, options={"render": {"line_numbers": False}})
    assert "| def compute_total" not in plain.context
    assert "\ndef compute_total" in plain.context
    assert plain.context_tokens == sum(c.token_count for c in plain.chunks)
    assert plain.config_id != response.config_id


def test_line_numbers_are_skipped_when_content_does_not_map_to_lines() -> None:
    exact = _chunk_candidate(1, "app/a.py")  # lines 11-15, one content line
    assert exact.record is not None
    exact.record = replace(exact.record, content="a\nb\nc\nd\ne")
    trimmed = _chunk_candidate(2, "docs/b.md")  # a doc chunk whose blank edges were trimmed
    assert trimmed.record is not None
    trimmed.record = replace(trimmed.record, content="heading\ntext")
    context = render_context([exact, trimmed], "worker", "rr_1", line_numbers=True)
    assert "11| a\n12| b\n13| c\n14| d\n15| e" in context
    assert "\nheading\ntext\n" in context
    assert chunk_cost(exact.record, True) > chunk_cost(exact.record, False) == 50
    assert chunk_cost(trimmed.record, True) == 50


def test_options_switch_retrievers_and_change_config_id(indexed: RetrievalService) -> None:
    lexical = retrieve(indexed, options={"retrievers": {"vector": False, "symbol": False}})
    hybrid = retrieve(indexed)
    assert set(lexical.stats["retrievers"]) == {"lexical"}
    assert lexical.config_id != hybrid.config_id


def test_changed_file_is_filtered_in_verify_mode(
    indexed: RetrievalService, workspace: Path
) -> None:
    parser = workspace / "app" / "parser.py"
    parser.write_text(parser.read_text() + "\n# edited after indexing\n")
    response = retrieve(indexed, freshness_mode="verify", cache="bypass", include_candidates=True)
    assert all(c.path != "app/parser.py" for c in response.chunks)
    assert response.stats["filtered_by_reason"].get("file_changed", 0) > 0
    assert any(c["filtered_reason"] == "file_changed" for c in response.candidates)


def test_sync_mode_reindexes_changed_file_before_retrieval(
    indexed: RetrievalService, workspace: Path
) -> None:
    parser = workspace / "app" / "parser.py"
    parser.write_text(parser.read_text() + "\n\ndef apply_discount(total):\n    return total\n")
    response = retrieve(
        indexed, freshness_mode="sync", query="apply_discount for invoice totals", task={}
    )
    assert any(c.symbol == "apply_discount" for c in response.chunks)
    assert response.snapshots["ws"]["code_generation"] == 2


def test_deleted_file_never_reaches_context(indexed: RetrievalService, workspace: Path) -> None:
    (workspace / "web" / "client.ts").unlink()
    response = retrieve(
        indexed,
        query="RetryPolicy shouldRetry attempts",
        task={},
        freshness_mode="verify",
        cache="bypass",
    )
    assert all(c.path != "web/client.ts" for c in response.chunks)


def test_expired_and_pinned_docs_are_filtered(service: RetrievalService) -> None:
    service.index(
        IndexRequest(
            corpus_id="kb",
            documents=[
                IndexDocument(
                    path="policy.md",
                    content="# Policy\n\nRefund window is 30 days.",
                    version="2",
                    valid_until="2000-01-01T00:00:00Z",
                ),
                IndexDocument(
                    path="faq.md", content="# FAQ\n\nRefund requests go to billing.", version="5"
                ),
            ],
        )
    )
    response = service.retrieve(RetrieveRequest(corpora=["kb"], query="refund window days"))
    assert [c.path for c in response.chunks] == ["faq.md"]
    pinned = service.retrieve(
        RetrieveRequest(
            corpora=["kb"],
            query="refund window days",
            cache="bypass",
            workspace_state={"doc_versions": {"faq.md": "4"}},
        )
    )
    assert pinned.chunks == []


def test_unknown_corpus_and_profile_are_client_errors(indexed: RetrievalService) -> None:
    with pytest.raises(RetrievalError) as missing:
        indexed.retrieve(RetrieveRequest(corpora=["nope"], query="x"))
    assert missing.value.status_code == 404
    with pytest.raises(RetrievalError):
        indexed.retrieve(RetrieveRequest(corpora=["ws"], profile="architect", query="x"))


# ------------------------------------------------------------------------- cache


def test_exact_cache_hit(indexed: RetrievalService) -> None:
    first = retrieve(indexed)
    second = retrieve(indexed)
    assert first.cache.status == "miss" and second.cache.status == "hit"
    assert second.cache.source_request_id == first.request_id
    assert [c.chunk_id for c in second.chunks] == [c.chunk_id for c in first.chunks]
    assert second.chunks[0].freshness == "verified"


def test_reformatted_reviewer_feedback_reuses_retrieval(indexed: RetrievalService) -> None:
    first = retrieve(indexed, review={"feedback": "compute_total still counts tax lines twice."})
    second = retrieve(
        indexed, review={"feedback": "  COMPUTE_TOTAL still counts tax lines twice!! "}
    )
    assert first.cache.status == "miss"
    assert second.cache.status in {"semantic_hit", "hit"}
    assert second.cache.status == "semantic_hit"


def test_new_anchor_in_feedback_triggers_new_retrieval(indexed: RetrievalService) -> None:
    retrieve(indexed, review={"feedback": "compute_total counts tax lines twice"})
    second = retrieve(
        indexed,
        review={"feedback": "compute_total counts tax lines twice; also update docs/guide.md"},
    )
    assert second.cache.status == "miss"
    assert second.cache.reason and second.cache.reason.startswith("query_changed")


def test_cache_is_scoped_per_task(indexed: RetrievalService) -> None:
    retrieve(indexed)
    other = retrieve(indexed, task={**TASK, "task_id": "T002"})
    assert other.cache.status == "miss"


def test_unrelated_change_revalidates_cached_result(
    indexed: RetrievalService, workspace: Path
) -> None:
    first = retrieve(indexed, budget={"max_chunks": 3})
    assert all(c.path != "web/client.ts" for c in first.chunks)
    (workspace / "web" / "client.ts").write_text(
        (workspace / "web" / "client.ts").read_text() + "\nexport const VERSION = 3;\n"
    )
    second = retrieve(indexed, budget={"max_chunks": 3})
    assert second.snapshots["ws"]["code_generation"] == 2
    assert second.cache.status == "revalidated_hit"
    assert [c.chunk_id for c in second.chunks] == [c.chunk_id for c in first.chunks]


def test_relevant_change_invalidates_cached_result(
    indexed: RetrievalService, workspace: Path
) -> None:
    retrieve(indexed, budget={"max_chunks": 3})
    (workspace / "app" / "tax.py").write_text(
        "def skip_tax_lines(lines):\n    return [l for l in lines if 'TAX' not in l]\n"
        "# tax lines must not be counted twice in invoice totals\n"
    )
    second = retrieve(indexed, budget={"max_chunks": 3})
    assert second.cache.status == "miss"
    assert second.cache.reason in {"delta_lexical_relevant", "delta_vector_relevant"}


def test_cache_bypass_and_refresh(indexed: RetrievalService) -> None:
    retrieve(indexed)
    assert retrieve(indexed, cache="bypass").cache.status == "bypass"
    assert retrieve(indexed, cache="refresh").cache.status == "refresh"


# ---------------------------------------------------------------------- reranker


@respx.mock
def test_http_reranker_scores_drive_final_order(settings: Settings, workspace: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode()
        import json

        payload = json.loads(body)
        assert set(payload) >= {"query", "documents", "top_n", "return_documents"}
        assert all(set(doc) == {"id", "text"} for doc in payload["documents"])
        results = []
        for rank, doc in enumerate(payload["documents"], start=1):
            score = 5.0 if "class RetryPolicy" in doc["text"] else -2.0
            results.append({"id": doc["id"], "score": score, "rank": rank})
        return httpx.Response(200, json={"results": results})

    respx.post("http://reranker:8200/v1/rerank").mock(side_effect=handler)
    service = make_service(settings, HttpReranker("http://reranker:8200", api_key="k"))
    try:
        service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        response = service.retrieve(
            RetrieveRequest(corpora=["ws"], query="retry policy attempts for payments")
        )
        top = response.chunks[0]
        assert top.symbol == "RetryPolicy"
        assert top.scores.reranker is not None and top.scores.reranker > 0.99
        assert top.scores.reranker_rank == 1
        assert not response.degraded
    finally:
        service.close()


@respx.mock
def test_reranker_failure_degrades_to_fused_order(settings: Settings, workspace: Path) -> None:
    respx.post("http://reranker:8200/v1/rerank").mock(return_value=httpx.Response(503))
    service = make_service(settings, HttpReranker("http://reranker:8200"))
    try:
        service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        response = service.retrieve(RetrieveRequest(corpora=["ws"], query="compute_total"))
        assert response.chunks
        assert any(r.startswith("reranker_unavailable") for r in response.degraded_reasons)
        assert response.degraded
        assert response.chunks[0].scores.reranker is None
    finally:
        service.close()


def test_disabled_reranker_is_not_degraded(settings: Settings, workspace: Path) -> None:
    service = make_service(settings)
    try:
        service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        response = service.retrieve(RetrieveRequest(corpora=["ws"], query="compute_total"))
        assert response.chunks
        assert not response.degraded
        assert response.degraded_reasons == []
        assert response.notes == ["reranker_disabled"]
        assert response.chunks[0].scores.reranker is None
    finally:
        service.close()


def test_disabled_reranker_not_degraded_on_cache_hit(settings: Settings, workspace: Path) -> None:
    service = make_service(settings)
    try:
        service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        first = service.retrieve(RetrieveRequest(corpora=["ws"], query="compute_total"))
        second = service.retrieve(RetrieveRequest(corpora=["ws"], query="compute_total"))
        assert first.cache.status == "miss"
        assert second.cache.status == "hit"
        assert not second.degraded
        assert second.degraded_reasons == []
        assert second.notes == ["reranker_disabled"]
    finally:
        service.close()


# ----------------------------------------------------------------------- logging


def test_every_candidate_is_logged_with_features(indexed: RetrievalService) -> None:
    response = retrieve(
        indexed, trace={"mission_id": "m1", "task_id": "T001", "attempt": 1, "role": "worker"}
    )
    rows = list(indexed.log.iter_feature_rows())
    assert rows and {r["request_id"] for r in rows} == {response.request_id}
    selected = [r for r in rows if r["selected"]]
    assert len(selected) == len(response.chunks)
    row = selected[0]
    for key in (
        "lexical_score",
        "vector_score",
        "fused_score",
        "exact_symbol",
        "path_mentioned",
        "source_type",
        "final_rank",
        "freshness",
        "deterministic_score",
    ):
        assert key in row
    assert row["trace"]["task_id"] == "T001"
    requests = indexed.log.requests()
    assert requests[0]["cache_status"] == "miss" and requests[0]["n_selected"] == len(
        response.chunks
    )


def test_context_names_the_file_under_the_corpus_root() -> None:
    chunk = _chunk_candidate(1, "tools/server/server-context.cpp")
    assert chunk.record is not None
    corpus = chunk.record.corpus_id
    with_root = render_context([chunk], "worker", "rr_1", roots={corpus: "/d/rag-tmp/src/"})
    expected = "| file=/d/rag-tmp/src/tools/server/server-context.cpp | lines="
    assert "| path=tools/server/server-context.cpp " + expected in with_root
    assert "| file=" not in render_context([chunk], "worker", "rr_1")
    assert "| file=" not in render_context([chunk], "worker", "rr_1", roots={corpus: None})


# ------------------------------------------------------------- related tests


def _test_candidate(n: int, path: str, content: str | None = None) -> Candidate:
    candidate = _chunk_candidate(n, path)
    if content is not None:
        candidate.record = replace(candidate.record, content=content)
    return candidate


def _related_candidates(
    source: Candidate,
    test_path: str,
    conftest_path: str = "tests/conftest.py",
    fake_path: str = "tests/fake_y.py",
) -> dict[str, list[Candidate]]:
    return {
        source.record.path: [
            _test_candidate(100, test_path),
            _test_candidate(101, conftest_path),
            _test_candidate(102, fake_path),
        ]
    }


def test_related_tests_are_appended_when_budget_allows() -> None:
    source = _chunk_candidate(1, "src/pkg/x.py")
    related = _related_candidates(source, "tests/test_x.py")
    selected = select_context(
        [source],
        BudgetConfig(),
        max_chunks=12,
        max_tokens=6000,
        related_tests=related,
    )
    paths = [c.record.path for c in selected]
    assert paths == ["src/pkg/x.py", "tests/test_x.py", "tests/conftest.py", "tests/fake_y.py"]
    assert all(c.selection_reason == "related_test" for c in selected[1:])
    assert selected[0].selection_reason == "selected"
    # Every related test scores below every original selection.
    floor = source.final
    assert all(c.final < floor for c in selected[1:])


def test_related_tests_are_capped_at_three_files() -> None:
    source = _chunk_candidate(1, "src/pkg/x.py")
    related = {
        source.record.path: [
            _test_candidate(100, "tests/test_x.py"),
            _test_candidate(101, "tests/conftest.py"),
            _test_candidate(102, "tests/fake_y.py"),
            _test_candidate(103, "tests/fake_z.py"),
        ]
    }
    selected = select_context(
        [source],
        BudgetConfig(),
        max_chunks=12,
        max_tokens=6000,
        related_tests=related,
    )
    added = [c for c in selected[1:]]
    assert len(added) == 3
    assert all(c.selection_reason == "related_test" for c in added)
    # The fourth related-test file was not added: exactly one original plus three related.
    assert len(selected) == 4
    # The capped candidate that was not selected.
    capped = [c for c in related[source.record.path] if not c.selected]
    assert len(capped) == 1
    assert capped[0].selection_reason == "related_test_cap"


def test_related_tests_never_replace_originals_when_budget_is_exhausted() -> None:
    source = _chunk_candidate(1, "src/pkg/x.py")
    # Fill the budget with one high-cost original fragment so no room remains:
    # source (50) + big (5950) = exactly max_tokens (6000), leaving 0 for related tests.
    big = _chunk_candidate(2, "src/pkg/big.py")
    big.record.token_count = 5950
    related = _related_candidates(source, "tests/test_x.py")
    selected = select_context(
        [source, big],
        BudgetConfig(),
        max_chunks=12,
        max_tokens=6000,
        related_tests=related,
    )
    # Both originals are kept; no related test fits into the leftover budget.
    assert [c.record.path for c in selected] == ["src/pkg/x.py", "src/pkg/big.py"]
    assert all(c.selection_reason == "selected" for c in selected)
    # The related tests were considered but excluded on token budget.
    for c in related["src/pkg/x.py"]:
        assert not c.selected
        assert c.selection_reason == "token_budget"


def test_related_tests_end_to_end_add_related_test_not_already_selected(
    settings: Settings, workspace: Path
) -> None:
    # Build a workspace where app/parser.py is selected as an original but its
    # related test (tests/test_parser.py) is not selected, so the related-test
    # expansion adds it into the leftover budget.
    (workspace / "app").mkdir(parents=True, exist_ok=True)
    (workspace / "tests").mkdir(exist_ok=True)
    (workspace / "app" / "parser.py").write_text(
        "def compute_total(lines):\n    return sum(1 for _ in lines)\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "test_parser.py").write_text(
        "from app.parser import compute_total\n\n\ndef test_total():\n"
        "    assert compute_total([1, 2]) == 3\n",
        encoding="utf-8",
    )
    service = make_service(settings)
    try:
        service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        response = service.retrieve(
            RetrieveRequest(
                corpora=["ws"],
                task={
                    "mission_id": "m1",
                    "task_id": "T001",
                    "title": "compute_total",
                    "objective": "compute_total must sum lines",
                    "scope": ["app/parser.py"],
                },
            )
        )
        paths = [c.path for c in response.chunks]
        assert "app/parser.py" in paths
        # The related test is added (it was not selected as an original).
        assert "tests/test_parser.py" in paths
        # Every related-test fragment renders below every original selection.
        original_final = max(c.scores.final for c in response.chunks if c.path == "app/parser.py")
        related_final = max(
            c.scores.final for c in response.chunks if c.path == "tests/test_parser.py"
        )
        assert related_final < original_final
    finally:
        service.close()


def test_related_test_paths_resolve_imported_fakes_against_store(
    settings: Settings, workspace: Path
) -> None:
    # Index a workspace with a source file, its test (which imports a fake), and the
    # fake itself; related_test_paths must resolve the imported fake to a real corpus path.
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "tests").mkdir(exist_ok=True)
    (workspace / "src" / "pkg").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "pkg" / "x.py").write_text(
        "def compute_total(lines):\n    return sum(1 for _ in lines)\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "test_x.py").write_text(
        "from src.pkg.x import compute_total\nfrom tests.fake_y import Y\n\n\n"
        "def test_total():\n    assert compute_total([1, 2]) == 3\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "conftest.py").write_text(
        "import pytest\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "fake_y.py").write_text(
        "class Y:\n    value = 1\n",
        encoding="utf-8",
    )
    service = make_service(settings)
    try:
        service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        related = related_test_paths(service.store, ["ws"], "src/pkg/x.py")
        # All three related files are resolved to real corpus paths.
        assert related == ["tests/test_x.py", "tests/conftest.py", "tests/fake_y.py"]
        # service.retrieve includes tests/fake_y.py when the budget allows.
        response = service.retrieve(
            RetrieveRequest(
                corpora=["ws"],
                task={
                    "mission_id": "m1",
                    "task_id": "T001",
                    "title": "compute_total",
                    "objective": "compute_total must sum lines",
                    "scope": ["src/pkg/x.py"],
                },
            )
        )
        paths = [c.path for c in response.chunks]
        assert "src/pkg/x.py" in paths
        assert "tests/test_x.py" in paths
        assert "tests/fake_y.py" in paths
        # Every related-test fragment scores below every original selection.
        original_final = max(c.scores.final for c in response.chunks if c.path == "src/pkg/x.py")
        for c in response.chunks:
            if c.path in ("tests/test_x.py", "tests/conftest.py", "tests/fake_y.py"):
                assert c.scores.final < original_final
    finally:
        service.close()


def test_related_test_paths_match_test_x_star_but_not_unrelated_names(
    settings: Settings, workspace: Path
) -> None:
    # tests/unit/test_x_parsing.py must be returned for src/pkg/x.py (stem starts with
    # test_x_), while tests/test_xyz.py must not (unrelated stem).
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "pkg").mkdir(parents=True, exist_ok=True)
    (workspace / "tests").mkdir(parents=True, exist_ok=True)
    (workspace / "tests" / "unit").mkdir(exist_ok=True)
    (workspace / "src" / "pkg" / "x.py").write_text(
        "def compute_total(lines):\n    return sum(1 for _ in lines)\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "unit" / "test_x_parsing.py").write_text(
        "from src.pkg.x import compute_total\n\n\n"
        "def test_total():\n    assert compute_total([1, 2]) == 3\n",
        encoding="utf-8",
    )
    (workspace / "tests" / "test_xyz.py").write_text(
        "def test_something():\n    assert True\n",
        encoding="utf-8",
    )
    service = make_service(settings)
    try:
        service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        related = related_test_paths(service.store, ["ws"], "src/pkg/x.py")
        # The test_x* file is matched; the unrelated test_xyz.py is not.
        assert "tests/unit/test_x_parsing.py" in related
        assert "tests/test_xyz.py" not in related
    finally:
        service.close()


def test_no_expansion_for_non_code_or_test_only_selections(
    settings: Settings, workspace: Path
) -> None:
    # _related_tests only expands for code files that are not themselves test files.
    service = make_service(settings)
    try:
        service.index(IndexRequest(corpus_id="ws", root=str(workspace)))
        # A doc file is not a code source, so no related tests are found for it.
        doc_candidate = _chunk_candidate(1, "docs/guide.md")
        doc_candidate.record.source_type = "doc"
        related = service._related_tests([doc_candidate], ["ws"], {})
        assert related == {}

        # A test file is itself a test, so no related tests are found for it.
        test_candidate = _chunk_candidate(1, "tests/test_parser.py")
        related = service._related_tests([test_candidate], ["ws"], {})
        assert related == {}

        # A code file that is not a test does expand.
        code_candidate = _chunk_candidate(1, "app/parser.py")
        related = service._related_tests([code_candidate], ["ws"], {})
        assert "app/parser.py" in related
        assert "tests/test_parser.py" in [c.record.path for c in related["app/parser.py"]]
    finally:
        service.close()


def test_related_tests_respect_max_chunks() -> None:
    source = _chunk_candidate(1, "src/pkg/x.py")
    related = _related_candidates(source, "tests/test_x.py")
    selected = select_context(
        [source],
        BudgetConfig(),
        max_chunks=2,
        max_tokens=6000,
        related_tests=related,
    )
    # max_chunks=2 keeps only the original and at most one related test.
    assert len(selected) <= 2
    assert selected[0].record.path == "src/pkg/x.py"
    if len(selected) == 2:
        assert selected[1].selection_reason == "related_test"


def test_token_budget_is_never_exceeded_with_related_tests() -> None:
    source = _chunk_candidate(1, "src/pkg/x.py")
    related = _related_candidates(source, "tests/test_x.py")
    # Tight budget: room for one related test but not all three.
    selected = select_context(
        [source],
        BudgetConfig(),
        max_chunks=12,
        max_tokens=200,
        related_tests=related,
    )
    total = sum(chunk_cost(c.record, False) for c in selected)
    assert total <= 200
    # Every added related test is below the originals and the budget is respected.
    for c in selected[1:]:
        assert c.selection_reason == "related_test"
        assert c.final < source.final
