"""Tests for the real-query eval wiring: dataset loading, the flat summary, and
fake-search metric and latency math (no network, no live index).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ahawr_retrieval.eval.ahawr_metrics import aggregate
from ahawr_retrieval.eval.cli import main as eval_main
from ahawr_retrieval.eval.datasets import EvalConfig, EvalDataset, JudgmentSpec
from ahawr_retrieval.eval.metrics import (
    DEFAULT_KS,
    Judgment,
    label_ranking,
    percentile,
    ranking_metrics,
)
from ahawr_retrieval.eval.runner import EvalRunner, run_evaluation
from ahawr_retrieval.models import CacheInfo, ChunkScores, RetrievedChunk, RetrieveResponse

REAL_JSONL = Path(__file__).resolve().parents[1] / "eval" / "data" / "real_queries.jsonl"
SUMMARY_KEYS = {"precision@5", "precision@8", "recall@5", "recall@8", "MRR", "p50_ms", "p95_ms"}


def test_real_dataset_loads_by_name_and_path() -> None:
    dataset = EvalDataset.load("real")
    assert dataset.dataset_id == "real"
    assert dataset.kind == "silver"
    assert len(dataset.tasks) == 90
    # repo root is three levels above eval/data/, matching load_jsonl
    assert Path(dataset.corpora[0].root) == REAL_JSONL.resolve().parents[3]
    for task in dataset.tasks:
        assert all(
            j.symbol is None and j.section is None and j.chunk_id is None for j in task.relevant
        )
    by_id = {t.id: t for t in dataset.tasks}
    for line in REAL_JSONL.open(encoding="utf-8"):
        record = json.loads(line)
        expected = [JudgmentSpec(path=p) for p in record["expected_paths"]]
        assert by_id[record["id"]].relevant == expected


def test_real_dataset_missing_file_raises() -> None:
    from ahawr_retrieval.eval import datasets

    saved = datasets._REAL_JSONL
    datasets._REAL_JSONL = Path("/nonexistent/real_queries.jsonl")
    try:
        with pytest.raises(FileNotFoundError):
            EvalDataset.load("real")
    finally:
        datasets._REAL_JSONL = saved


def test_load_jsonl_requires_expected_paths(tmp_path: Path) -> None:
    (tmp_path / "bad.jsonl").write_text('{"id": "t1", "expected_paths": []}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="no expected_paths"):
        EvalDataset.load_jsonl(tmp_path / "bad.jsonl")


class FakeService:
    """Stands in for RetrievalService: fixed ranking, fixed per-call latency."""

    def __init__(self, paths: list[str], latencies: list[float]) -> None:
        self._paths = paths
        self._latencies = list(latencies)

    def retrieve(self, request: object) -> RetrieveResponse:
        return _response(self._paths, self._latencies.pop(0))

    def close(self) -> None:
        pass


def _response(paths: list[str], latency_ms: float) -> RetrieveResponse:
    return RetrieveResponse(
        request_id="req-1",
        profile="worker",
        config_id="hashing",
        query={"text": ""},
        cache=CacheInfo(status="bypass"),
        snapshots={},
        degraded=False,
        degraded_reasons=[],
        chunks=[
            RetrievedChunk(
                rank=i,
                chunk_id=f"{path}#{i}",
                corpus_id="repo",
                source_type="code",
                authority="repo",
                path=path,
                document=path,
                start_line=1,
                end_line=2,
                content=path,
                content_hash="h",
                file_hash="f",
                version="1",
                generation=1,
                indexed_at=0.0,
                token_count=1,
                freshness="verified",
                scores=ChunkScores(),
            )
            for i, path in enumerate(paths, 1)
        ],
        context="",
        context_tokens=0,
        stats={"backend": "sqlite"},
        timings_ms={"total": latency_ms},
        candidates=[
            {"path": p, "final_score": 1.0 - i / 10.0, "final_rank": i}
            for i, p in enumerate(paths, 1)
        ],
    )


def test_summary_metrics_with_fake_search(tmp_path: Path) -> None:
    dataset = EvalDataset.model_validate(
        {
            "dataset_id": "fake",
            "version": "1",
            "kind": "gold",
            "corpora": [{"corpus_id": "repo", "root": "/tmp/none"}],
            "ks": [5, 8],
            "tasks": [
                {
                    "id": "t1",
                    "profile": "worker",
                    "query": "find the retry policy",
                    "relevant": [
                        JudgmentSpec(path="a.py"),
                        JudgmentSpec(path="b.py"),
                    ],
                }
            ],
        }
    )
    config = EvalConfig(name="fake", embedder={"kind": "hashing"})
    runner = EvalRunner(dataset, tmp_path / "work", ks=(1, 3, 5, 8, 10), freshness_mode="verify")
    fake = FakeService(
        ["x.py", "a.py", "y.py", "b.py", "z.py", "w.py", "v.py", "u.py"],
        [100.0, 200.0, 300.0, 400.0],
    )
    task = dataset.tasks[0]
    rows = [
        runner._run_task(fake, config, task)  # type: ignore[arg-type]  # duck-typed fake
        for _ in range(4)
    ]
    cache = {"cache_hit_ratio": 0.0, "decision_accuracy": 0.0, "retrieval_calls_per_task": 1.0}
    summary = runner._aggregate(rows, cache, dataset.ks)["summary"]
    assert set(summary) == SUMMARY_KEYS
    # 2 of the 8 retrieved files are relevant, both within the top 5
    assert summary["precision@5"] == pytest.approx(2 / 5)
    assert summary["precision@8"] == pytest.approx(2 / 8)
    assert summary["recall@5"] == pytest.approx(1.0)
    assert summary["recall@8"] == pytest.approx(1.0)
    assert summary["MRR"] == pytest.approx(0.5)
    # nearest-rank percentiles of [100, 200, 300, 400] per task:
    # p50 is 200 (rank 2), p95 is 400 (rank 4)
    assert summary["p50_ms"] == pytest.approx(200.0)
    assert summary["p95_ms"] == pytest.approx(400.0)
    # flat keys also appear in the per-task file_* metrics
    assert rows[0]["metrics"]["file_Precision@8"] == pytest.approx(2 / 8)


def test_runner_result_carries_summary(tmp_path: Path, workspace: Path) -> None:
    data = {
        "dataset_id": "unit-real",
        "version": "1",
        "kind": "silver",
        "corpora": [{"corpus_id": "ws", "root": str(workspace)}],
        "ks": [5, 8],
        "tasks": [
            {
                "id": "G1",
                "query": "compute_total double counts tax",
                "relevant": [{"path": "app/parser.py"}],
            }
        ],
    }
    path = tmp_path / "real.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    dataset = EvalDataset.load(path)
    configs = [EvalConfig(name="hybrid", embedder={"kind": "hashing"})]
    results = run_evaluation(dataset, configs, tmp_path / "out")
    summary = results["hybrid"]["aggregate"]["summary"]
    assert set(summary) == SUMMARY_KEYS
    # file-level matching: the expected path is app/parser.py
    assert summary["recall@5"] > 0.0


def test_cli_run_writes_flat_summary_json(tmp_path: Path, workspace: Path) -> None:
    data = {
        "dataset_id": "unit-real",
        "version": "1",
        "kind": "silver",
        "corpora": [{"corpus_id": "ws", "root": str(workspace)}],
        "ks": [5, 8],
        "tasks": [
            {
                "id": "G1",
                "query": "compute_total double counts tax",
                "relevant": [{"path": "app/parser.py"}],
            }
        ],
    }
    dataset_path = tmp_path / "real.json"
    dataset_path.write_text(json.dumps(data), encoding="utf-8")
    config_path = tmp_path / "cfg.json"
    config_path.write_text(
        json.dumps({"name": "hybrid", "embedder": {"kind": "hashing"}}), encoding="utf-8"
    )
    out = tmp_path / "out"
    summary_out = tmp_path / "summary.json"
    assert (
        eval_main(
            [
                "run",
                "--dataset",
                str(dataset_path),
                "--config",
                str(config_path),
                "--out",
                str(out),
                "--summary-out",
                str(summary_out),
            ]
        )
        == 0
    )
    written = json.loads(summary_out.read_text(encoding="utf-8"))
    assert set(written) == {"hybrid"}
    assert set(written["hybrid"]) == SUMMARY_KEYS


def test_metric_math_and_percentiles_match_aggregate() -> None:
    judgments = [Judgment("a.py"), Judgment("b.py")]
    ranking = [{"path": p} for p in ["x", "a.py", "y", "b.py", "z", "w", "v", "u"]]
    m = ranking_metrics(ranking, judgments, ks=(5, 8), file_level=True)
    assert m["Precision@5"] == pytest.approx(2 / 5)
    assert m["Precision@8"] == pytest.approx(2 / 8)
    assert m["Recall@5"] == pytest.approx(1.0)
    assert m["Recall@8"] == pytest.approx(1.0)
    assert m["MRR"] == pytest.approx(0.5)
    # label_ranking is the shared matcher: file-level judgments count each path once
    labels = label_ranking(ranking, judgments, file_level=True)
    assert [k for k, _ in labels] == [
        None, "a.py::::::", None, "b.py::::::", None, None, None, None
    ]
    # nearest-rank percentiles as used by the runner
    values = [100.0, 200.0, 300.0, 400.0]
    assert percentile(values, 0.5) == pytest.approx(200.0)
    assert percentile(values, 0.95) == pytest.approx(400.0)
    # ahawr_metrics.aggregate carries the same percentile for task-level latency
    outcomes = [
        {
            "label": "all",
            "task_id": "T1",
            "attempts": 1,
            "first_pass": True,
            "final_success": True,
            "latency_s": 0.4,
        },
        {
            "label": "all",
            "task_id": "T2",
            "attempts": 1,
            "first_pass": False,
            "final_success": True,
            "latency_s": 0.8,
        },
    ]
    report = aggregate(outcomes)
    assert report["all"]["total_task_latency_s_p95"] == pytest.approx(0.8)


def test_existing_datasets_unaffected(tmp_path: Path) -> None:
    # the gold loader path is unchanged: JSON datasets still load, and relative
    # corpus roots still resolve
    data = {
        "dataset_id": "unit-gold",
        "version": "1",
        "kind": "gold",
        "corpora": [{"corpus_id": "ws", "root": "ws"}],
        "tasks": [
            {
                "id": "G1",
                "query": "q",
                "relevant": [{"path": "app/parser.py", "symbol": "compute_total"}],
            }
        ],
    }
    path = tmp_path / "gold.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = EvalDataset.load(path)
    assert loaded.tasks[0].relevant[0].symbol == "compute_total"
    # 8 was not added to DEFAULT_KS
    assert 8 not in DEFAULT_KS