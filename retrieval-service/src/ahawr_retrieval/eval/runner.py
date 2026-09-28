"""Runs one dataset against several retrieval configurations on an identical index.

For every configuration and task the runner records ranking metrics (chunk- and file-level),
context recall, latency, reranker latency, context tokens and degradations. Tasks with retry
variants are then replayed through the cache to measure cache hit ratio and whether reuse
decisions match the expected ones (``reuse`` vs ``reretrieve``).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..config import Settings
from ..embeddings import (
    DEFAULT_LOCAL_EMBEDDING_MODEL,
    Embedder,
    HashingEmbedder,
    LocalEmbedder,
    OpenAICompatibleEmbedder,
)
from ..models import IndexRequest, RetrieveRequest
from ..reranker import HttpReranker, LocalReranker, NoopReranker, Reranker
from ..service import RetrievalService
from ..text import short_hash
from .datasets import EvalConfig, EvalDataset, EvalTask
from .metrics import DEFAULT_KS, label_ranking, mean, percentile, ranking_metrics

CACHE_HITS = {"hit", "semantic_hit", "revalidated_hit"}


def _model_dir() -> str | None:
    # the built-in CPU models baked into the container image (see Dockerfile)
    return os.environ.get("RETRIEVAL_RERANKER_MODEL_DIR") or None


def _embedder(spec: dict[str, Any]) -> Embedder:
    kind = spec.get("kind", "hashing")
    if kind == "hashing":
        return HashingEmbedder(int(spec.get("dim", 384)))
    if kind == "openai":
        return OpenAICompatibleEmbedder(
            spec["url"],
            spec["model"],
            api_key=spec.get("api_key"),
            query_prefix=spec.get("query_prefix", ""),
            passage_prefix=spec.get("passage_prefix", ""),
        )
    if kind == "local":
        return LocalEmbedder(
            spec.get("model", DEFAULT_LOCAL_EMBEDDING_MODEL),
            cache_dir=_model_dir(),
            query_prefix=spec.get("query_prefix", "query: "),
            passage_prefix=spec.get("passage_prefix", "passage: "),
        )
    raise ValueError(f"unknown embedder kind {kind!r}")


def _reranker(config: EvalConfig) -> Reranker:
    if config.reranker_url:
        return HttpReranker(config.reranker_url, api_key=config.reranker_api_key)
    if config.reranker_model:
        return LocalReranker(config.reranker_model, cache_dir=_model_dir())
    return NoopReranker()


class EvalRunner:
    def __init__(
        self,
        dataset: EvalDataset,
        work_dir: str | Path,
        ks: tuple[int, ...] = DEFAULT_KS,
        freshness_mode: str = "verify",
    ) -> None:
        self.dataset = dataset
        self.work_dir = Path(work_dir)
        self.ks = ks
        self.freshness_mode = freshness_mode

    def _service(self, config: EvalConfig) -> tuple[RetrievalService, dict[str, Any]]:
        # Configurations sharing an embedder and backend share one index directory, so they are
        # compared on byte-identical chunks and snapshots.
        index_key = short_hash(json.dumps([config.embedder, config.rag], sort_keys=True), length=12)
        settings = Settings(
            data_dir=self.work_dir / f"index-{index_key}",
            allowed_roots=[c.root for c in self.dataset.corpora],
            sync_min_interval_seconds=3600,
            max_file_bytes=1024 * 1024,
        )
        if config.rag:
            settings = replace(settings, **{f"rag_{k}": v for k, v in config.rag.items()})
        service = RetrievalService(
            settings, embedder=_embedder(config.embedder), reranker=_reranker(config)
        )
        index_stats: dict[str, Any] = {}
        for corpus in self.dataset.corpora:
            result = service.index(
                IndexRequest(
                    corpus_id=corpus.corpus_id,
                    root=corpus.root,
                    source_types=corpus.source_types,
                    exclude_globs=corpus.exclude_globs,
                )
            )
            index_stats[corpus.corpus_id] = {
                "code_snapshot": result.code_snapshot,
                "docs_snapshot": result.docs_snapshot,
                "git_head": result.git_head,
                "degraded_reasons": result.degraded_reasons,
            }
        return service, index_stats

    def _request(self, task: EvalTask, config: EvalConfig, **extra: Any) -> RetrieveRequest:
        review = dict(task.review or {})
        if "feedback" in extra:
            review["feedback"] = extra.pop("feedback")
        options = {"freshness_mode": self.freshness_mode, **config.options}
        return RetrieveRequest(
            profile=task.profile,
            corpora=[c.corpus_id for c in self.dataset.corpora],
            task={"mission_id": f"eval:{self.dataset.dataset_id}", "task_id": task.id, **task.task},
            review=review or None,
            query=task.query,
            options=options,
            trace={"label": config.name, "task_id": task.id, "role": task.profile},
            **extra,
        )

    def run_config(self, config: EvalConfig) -> dict[str, Any]:
        service, index_stats = self._service(config)
        try:
            tasks = [self._run_task(service, config, task) for task in self.dataset.tasks]
            cache = self._run_cache(service, config)
        finally:
            service.close()
        return {
            "config": config.model_dump(exclude={"reranker_api_key"}),
            "dataset": {
                "id": self.dataset.dataset_id,
                "version": self.dataset.version,
                "kind": self.dataset.kind,
                "tasks": len(self.dataset.tasks),
            },
            "index": index_stats,
            "aggregate": self._aggregate(tasks, cache),
            "cache": cache,
            "tasks": tasks,
            "generated_at": time.time(),
        }

    def _run_task(
        self, service: RetrievalService, config: EvalConfig, task: EvalTask
    ) -> dict[str, Any]:
        request = self._request(task, config, cache="bypass", include_candidates=True)
        started = time.perf_counter()
        response = service.retrieve(request)
        wall_ms = (time.perf_counter() - started) * 1000
        ranking = sorted(
            (c for c in response.candidates or [] if not c.get("filtered_reason")),
            key=lambda c: c["final_rank"] or 10**9,
        )
        judgments = task.judgments()
        selected = [c.model_dump() for c in response.chunks]
        context_labels = label_ranking(selected, judgments)
        relevant = [j for j in judgments if j.grade > 0]
        found = {key for key, gain in context_labels if key and gain > 0}
        metrics = ranking_metrics(ranking, judgments, self.ks)
        metrics.update(
            {
                f"file_{name}": value
                for name, value in ranking_metrics(
                    ranking, judgments, self.ks, file_level=True
                ).items()
            }
        )
        metrics["ContextRecall"] = len(found) / len(relevant) if relevant else 0.0
        return {
            "task_id": task.id,
            "profile": task.profile,
            "weak": task.weak,
            "tags": task.tags,
            "metrics": metrics,
            "latency_ms": response.timings_ms.get("total", wall_ms),
            "reranker_ms": response.timings_ms.get("rerank"),
            "context_tokens": response.context_tokens,
            "degraded_reasons": response.degraded_reasons,
            "backend": response.stats.get("backend"),
            "top": [
                {
                    "rank": i,
                    "path": c["path"],
                    "symbol": c.get("symbol"),
                    "section": c.get("section"),
                    "final": c["final_score"],
                    "relevant": label is not None,
                }
                for i, (c, (label, _)) in enumerate(
                    zip(ranking[:10], label_ranking(ranking[:10], judgments), strict=True), 1
                )
            ],
        }

    def _run_cache(self, service: RetrievalService, config: EvalConfig) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        calls = 0
        for task in self.dataset.tasks:
            calls += 1
            if not task.variants:
                continue
            base_feedback = (task.review or {}).get("feedback", "")
            service.retrieve(self._request(task, config, cache="refresh", feedback=base_feedback))
            for variant in task.variants:
                calls += 1
                response = service.retrieve(
                    self._request(task, config, cache="use", feedback=variant.feedback)
                )
                reused = response.cache.status in CACHE_HITS
                rows.append(
                    {
                        "task_id": task.id,
                        "variant": variant.id,
                        "expect": variant.expect,
                        "status": response.cache.status,
                        "reason": response.cache.reason,
                        "correct": reused == (variant.expect == "reuse"),
                    }
                )
        hits = sum(r["status"] in CACHE_HITS for r in rows)
        return {
            "variant_calls": len(rows),
            "cache_hit_ratio": hits / len(rows) if rows else 0.0,
            "decision_accuracy": sum(r["correct"] for r in rows) / len(rows) if rows else 0.0,
            "false_reuse": sum(
                bool(r["expect"] == "reretrieve" and r["status"] in CACHE_HITS) for r in rows
            ),
            "retrieval_calls_per_task": calls / max(len(self.dataset.tasks), 1),
            "variants": rows,
        }

    @staticmethod
    def _aggregate(tasks: list[dict[str, Any]], cache: dict[str, Any]) -> dict[str, Any]:
        latencies = [t["latency_ms"] for t in tasks]
        rerank = [t["reranker_ms"] for t in tasks if t["reranker_ms"] is not None]
        strong = [t for t in tasks if not t["weak"]]
        result: dict[str, Any] = {
            "retrieval": mean(t["metrics"] for t in tasks),
            "system": {
                "retrieval_latency_ms_mean": sum(latencies) / len(latencies) if latencies else 0,
                "retrieval_latency_ms_p50": percentile(latencies, 0.5),
                "retrieval_latency_ms_p95": percentile(latencies, 0.95),
                "reranker_latency_ms_mean": sum(rerank) / len(rerank) if rerank else None,
                "context_tokens_mean": sum(t["context_tokens"] for t in tasks) / len(tasks),
                "degraded_rate": sum(bool(t["degraded_reasons"]) for t in tasks) / len(tasks),
                "cache_hit_ratio": cache["cache_hit_ratio"],
                "cache_decision_accuracy": cache["decision_accuracy"],
                "retrieval_calls_per_task": cache["retrieval_calls_per_task"],
            },
            "by_profile": {
                profile: mean(t["metrics"] for t in tasks if t["profile"] == profile)
                for profile in sorted({t["profile"] for t in tasks})
            },
        }
        if strong and len(strong) != len(tasks):
            result["strong_only"] = mean(t["metrics"] for t in strong)
        return result


def run_evaluation(
    dataset: EvalDataset,
    configs: list[EvalConfig],
    out_dir: str | Path,
    ks: tuple[int, ...] = DEFAULT_KS,
    freshness_mode: str = "verify",
) -> dict[str, dict[str, Any]]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    runner = EvalRunner(dataset, out / "work", ks, freshness_mode)
    results: dict[str, dict[str, Any]] = {}
    for config in configs:
        result = runner.run_config(config)
        results[config.name] = result
        (out / f"{config.name}.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return results
