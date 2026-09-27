import json
from pathlib import Path

import pytest

from ahawr_retrieval.eval.ahawr_metrics import aggregate, load_rows, review_status, task_outcomes
from ahawr_retrieval.eval.cli import main as eval_main
from ahawr_retrieval.eval.datasets import EvalConfig, EvalDataset
from ahawr_retrieval.eval.metrics import Judgment, ranking_metrics
from ahawr_retrieval.eval.report import markdown
from ahawr_retrieval.eval.runner import run_evaluation
from ahawr_retrieval.eval.silver import build_silver


def chunk(path: str, symbol: str | None = None, section: str | None = None) -> dict[str, str]:
    return {"path": path, "symbol": symbol or "", "section": section or "", "chunk_id": path}


def test_ranking_metrics_hand_computed() -> None:
    judgments = [Judgment("a.py", 2, symbol="f"), Judgment("b.md", 1, section="Setup")]
    ranking = [
        chunk("x.py"),
        chunk("a.py", "f"),
        chunk("a.py", "f#part2"),
        chunk("b.md", section="Guide > Setup"),
    ]
    m = ranking_metrics(ranking, judgments, ks=(1, 3, 5))
    assert m["Recall@1"] == 0.0 and m["Recall@3"] == 0.5 and m["Recall@5"] == 1.0
    assert m["Precision@3"] == pytest.approx(2 / 3)  # chunk-level: duplicate symbol chunk counts
    assert m["MRR"] == 0.5
    # DCG@5 = 3/log2(3) + 1/log2(5) ; IDCG = 3/1 + 1/log2(3)
    import math

    expected = (3 / math.log2(3) + 1 / math.log2(5)) / (3 + 1 / math.log2(3))
    assert m["nDCG@5"] == pytest.approx(expected)
    file_level = ranking_metrics(ranking, judgments, ks=(1,), file_level=True)
    assert file_level["MRR"] == 0.5


def test_symbol_judgment_matches_methods_and_parts() -> None:
    judgment = Judgment("a.py", symbol="Cache")
    assert judgment.matches(chunk("a.py", "Cache.store"))
    assert judgment.matches(chunk("a.py", "Cache#body1"))
    assert not judgment.matches(chunk("a.py", "CacheHelper"))


def dataset_file(tmp_path: Path, workspace: Path) -> Path:
    data = {
        "dataset_id": "unit-gold",
        "version": "1",
        "kind": "gold",
        "corpora": [{"corpus_id": "ws", "root": str(workspace)}],
        "tasks": [
            {
                "id": "G1",
                "profile": "worker",
                "task": {"title": "Fix totals", "objective": "compute_total double counts tax"},
                "relevant": [{"path": "app/parser.py", "symbol": "compute_total", "grade": 2}],
                "variants": [
                    {"id": "fmt", "feedback": "", "expect": "reuse"},
                ],
            },
            {
                "id": "G2",
                "profile": "worker",
                "query": "how many attempts does the payment retry policy allow",
                "relevant": [
                    {"path": "web/client.ts", "symbol": "RetryPolicy", "grade": 2},
                    {"path": "docs/guide.md", "section": "Payment retries"},
                ],
                "review": {"feedback": "RetryPolicy ignores attempts"},
                "variants": [
                    {
                        "id": "rephrase",
                        "feedback": "  retrypolicy IGNORES attempts. ",
                        "expect": "reuse",
                    },
                    {
                        "id": "new-file",
                        "feedback": "RetryPolicy ignores attempts; see app/parser.py",
                        "expect": "reretrieve",
                    },
                ],
            },
        ],
    }
    path = tmp_path / "gold.json"
    path.write_text(json.dumps(data))
    return path


def test_runner_compares_configurations(tmp_path: Path, workspace: Path) -> None:
    dataset = EvalDataset.load(dataset_file(tmp_path, workspace))
    configs = [
        EvalConfig(name="lexical", options={"retrievers": {"vector": False, "symbol": False}}),
        EvalConfig(name="hybrid"),
    ]
    results = run_evaluation(dataset, configs, tmp_path / "out")
    assert set(results) == {"lexical", "hybrid"}
    hybrid = results["hybrid"]["aggregate"]
    assert hybrid["retrieval"]["Recall@10"] > 0.5
    assert hybrid["system"]["retrieval_calls_per_task"] == 2.5
    cache = results["hybrid"]["cache"]
    assert cache["variant_calls"] == 3
    assert cache["false_reuse"] == 0
    assert cache["decision_accuracy"] == 1.0
    assert (tmp_path / "out" / "hybrid.json").exists()
    report = markdown(results, "lexical")
    assert "| Recall@5 |" in report and "hybrid" in report
    # both configs saw the same snapshot
    snap = {name: r["index"]["ws"]["code_snapshot"] for name, r in results.items()}
    assert snap["lexical"] == snap["hybrid"]


def test_cli_run_validate_and_compare(
    tmp_path: Path, workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dataset = dataset_file(tmp_path, workspace)
    config = tmp_path / "hybrid.json"
    config.write_text(json.dumps({"name": "hybrid", "reranker_url": "${NO_SUCH_VAR:-}"}))
    assert eval_main(["validate-dataset", "--dataset", str(dataset)]) == 0
    out = tmp_path / "run"
    assert (
        eval_main(["run", "--dataset", str(dataset), "--config", str(config), "--out", str(out)])
        == 0
    )
    assert (out / "report.md").exists() and (out / "comparison.json").exists()
    assert eval_main(["compare", str(out / "hybrid.json"), "--baseline", "hybrid"]) == 0
    assert "Retrieval evaluation" in capsys.readouterr().out


def test_validate_reports_unresolved_labels(tmp_path: Path, workspace: Path) -> None:
    data = json.loads(dataset_file(tmp_path, workspace).read_text())
    data["tasks"][0]["relevant"] = [{"path": "app/parser.py", "symbol": "missing_symbol"}]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(data))
    assert eval_main(["validate-dataset", "--dataset", str(bad)]) == 1


ATTEMPTS = [
    {
        "state_key": "m1",
        "task_id": "T1",
        "task_attempt": 1,
        "event_type": "worker_completed",
        "worker_output": "FIX APPLIED: changed app/parser.py",
        "createdAt": "2026-09-01T10:00:00Z",
    },
    {
        "state_key": "m1",
        "task_id": "T1",
        "task_attempt": 1,
        "event_type": "reviewer_completed",
        "reviewer_output": 'ok {"status":"pass","score":90,"reason":"r","next_task":""}',
        "createdAt": "2026-09-01T10:05:00Z",
    },
    {
        "state_key": "m1",
        "task_id": "T2",
        "task_attempt": 1,
        "event_type": "reviewer_completed",
        "reviewer_output": '{"status":"needs_changes","score":40,"reason":"r","next_task":"x"}',
        "createdAt": "2026-09-01T11:00:00Z",
    },
    {
        "state_key": "m1",
        "task_id": "T2",
        "task_attempt": 2,
        "event_type": "worker_completed",
        "worker_output": "FIX APPLIED: updated web/client.ts RetryPolicy",
        "createdAt": "2026-09-01T11:10:00Z",
    },
    {
        "state_key": "m1",
        "task_id": "T2",
        "task_attempt": 2,
        "event_type": "reviewer_completed",
        "reviewer_output": '```json\n{"status":"pass","score":85,"reason":"r","next_task":""}\n```',
        "createdAt": "2026-09-01T11:20:00Z",
    },
    {
        "state_key": "m2",
        "task_id": "T1",
        "task_attempt": 3,
        "event_type": "reviewer_completed",
        "reviewer_output": '{"status":"needs_changes","score":10,"reason":"r","next_task":"y"}',
    },
]


def test_review_status_parsing() -> None:
    assert review_status('text {"status": "PASS", "score": 1}') == "pass"
    assert review_status('{"a": "{"} {"status":"needs_changes"}') == "needs_changes"
    assert review_status("no json") is None


def test_ahawr_outcome_metrics() -> None:
    outcomes = task_outcomes(ATTEMPTS, labels={"m2": "retrieval-off"}, default_label="hybrid")
    report = aggregate(outcomes)
    hybrid = report["hybrid"]
    assert hybrid["tasks"] == 2
    assert hybrid["first_pass_rate"] == 0.5
    assert hybrid["retry_rate"] == 0.5
    assert hybrid["avg_retries_per_task"] == 0.5
    assert hybrid["final_task_success_rate"] == 1.0
    assert hybrid["total_task_latency_s_mean"] == pytest.approx((300 + 1200) / 2)
    off = report["retrieval-off"]
    assert off["final_task_success_rate"] == 0.0 and off["avg_retries_per_task"] == 2


def test_ahawr_metrics_join_retrieval_logs(indexed, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    from ahawr_retrieval.eval.ahawr_metrics import retrieval_usage
    from ahawr_retrieval.models import RetrieveRequest

    for _ in range(2):
        indexed.retrieve(
            RetrieveRequest(
                corpora=["ws"],
                task={"mission_id": "m1", "task_id": "T1", "title": "totals"},
                trace={"state_namespace": "m1", "task_id": "T1"},
            )
        )
    usage = retrieval_usage(indexed.settings.log_path)
    report = aggregate(task_outcomes(ATTEMPTS[:2]), usage)
    assert report["all"]["retrieval_calls_per_task"] == 2
    assert report["all"]["cache_hit_ratio"] == 0.5


def test_load_rows_csv_and_json(tmp_path: Path) -> None:
    csv_file = tmp_path / "a.csv"
    csv_file.write_text("state_key;task_id;task_attempt\nm;T;1\n", encoding="utf-8")
    assert load_rows(csv_file)[0]["task_id"] == "T"
    json_file = tmp_path / "a.json"
    json_file.write_text(json.dumps({"data": [{"json": {"task_id": "T"}}]}))
    assert load_rows(json_file) == [{"task_id": "T"}]


def test_build_silver(workspace: Path) -> None:
    state = [
        {
            "state_key": "m1",
            "plan_json": json.dumps(
                {
                    "tasks": [
                        {
                            "id": "T1",
                            "title": "Fix totals",
                            "objective": "o",
                            "scope": ["app/parser.py"],
                        },
                        {"id": "T2", "title": "Retry", "objective": "o", "scope": []},
                    ]
                }
            ),
        }
    ]
    silver = build_silver(
        ATTEMPTS, state, "ws", workspace, changes={"m1/T2": ["web/client.ts", "missing.py"]}
    )
    ids = {t["id"] for t in silver["tasks"]}
    assert ids == {"S-m1-T1", "S-m1-T1-review", "S-m1-T2", "S-m1-T2-review"}
    t2 = next(t for t in silver["tasks"] if t["id"] == "S-m1-T2")
    assert t2["relevant"] == [{"path": "web/client.ts", "grade": 1}]
    assert t2["weak"] is True and "changes_map" in t2["tags"]
    t1 = next(t for t in silver["tasks"] if t["id"] == "S-m1-T1")
    assert t1["relevant"] == [{"path": "app/parser.py", "grade": 1}]
    EvalDataset.model_validate(silver)  # schema-compatible with the runner


def test_module_and_enclosing_symbol_judgments() -> None:
    module = Judgment("a.py", symbol="<module>")
    assert module.matches(chunk("a.py")) and not module.matches(chunk("a.py", "f"))
    method = Judgment("a.py", symbol="Cache.store")
    assert method.matches(chunk("a.py", "Cache"))  # small class kept in one chunk
    assert not method.matches(chunk("a.py", "Other"))
