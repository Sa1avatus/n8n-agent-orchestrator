"""``ahawr-retrieval-eval``: run, compare, validate datasets, AHAWR metrics, silver sets."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from .ahawr_metrics import aggregate, load_rows, retrieval_usage, task_outcomes
from .datasets import EvalConfig, EvalDataset
from .report import compare, markdown
from .runner import run_evaluation
from .silver import build_silver


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ahawr-retrieval-eval")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="evaluate configurations on one dataset")
    run.add_argument("--dataset", required=True)
    run.add_argument("--config", action="append", required=True, help="repeatable")
    run.add_argument("--baseline", help="config name used as baseline (default: first)")
    run.add_argument("--out", required=True)
    run.add_argument("--freshness-mode", default="verify", choices=["trust", "verify", "sync"])

    cmp_ = sub.add_parser("compare", help="compare saved result files")
    cmp_.add_argument("results", nargs="+")
    cmp_.add_argument("--baseline", required=True)
    cmp_.add_argument("--out")

    validate = sub.add_parser("validate-dataset", help="check every judgment resolves to chunks")
    validate.add_argument("--dataset", required=True)

    metrics = sub.add_parser("ahawr-metrics", help="first-pass/retry/success from AHAWR exports")
    metrics.add_argument("--attempts", required=True, help="Task Attempts export (JSON or CSV)")
    metrics.add_argument("--label", default="all", help="label for rows without a label")
    metrics.add_argument("--label-map", help="JSON {state_key: label}")
    metrics.add_argument("--retrieval-log", help="retrieval_logs.sqlite to join usage")

    silver = sub.add_parser("build-silver", help="build a silver dataset from AHAWR history")
    silver.add_argument("--attempts", required=True)
    silver.add_argument("--state", required=True, help="Task State export with plan_json")
    silver.add_argument("--root", required=True)
    silver.add_argument("--corpus-id", required=True)
    silver.add_argument("--changes", help='JSON {"<state_key>/<task_id>": [paths]}')
    silver.add_argument("--no-reviewer", action="store_true")
    silver.add_argument("--out", required=True)

    args = parser.parse_args(argv)

    if args.command == "run":
        dataset = EvalDataset.load(args.dataset)
        configs = [EvalConfig.load(p) for p in args.config]
        results = run_evaluation(dataset, configs, args.out, freshness_mode=args.freshness_mode)
        baseline = args.baseline or configs[0].name
        _write_reports(results, baseline, Path(args.out))
        print((Path(args.out) / "report.md").read_text(encoding="utf-8"))
        return 0

    if args.command == "compare":
        results = {}
        for path in args.results:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            results[data["config"]["name"]] = data
        if args.out:
            _write_reports(results, args.baseline, Path(args.out))
        print(markdown(results, args.baseline))
        return 0

    if args.command == "validate-dataset":
        return _validate(EvalDataset.load(args.dataset))

    if args.command == "ahawr-metrics":
        labels = json.loads(Path(args.label_map).read_text()) if args.label_map else None
        outcomes = task_outcomes(load_rows(args.attempts), labels, args.label)
        usage = retrieval_usage(args.retrieval_log) if args.retrieval_log else None
        print(json.dumps(aggregate(outcomes, usage), indent=2))
        return 0

    changes = json.loads(Path(args.changes).read_text()) if args.changes else None
    silver_dataset = build_silver(
        load_rows(args.attempts),
        load_rows(args.state),
        args.corpus_id,
        args.root,
        changes,
        include_reviewer=not args.no_reviewer,
    )
    Path(args.out).write_text(
        json.dumps(silver_dataset, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(json.dumps(silver_dataset["provenance"], indent=2), file=sys.stderr)
    print(f"{len(silver_dataset['tasks'])} silver tasks written to {args.out}", file=sys.stderr)
    return 0


def _write_reports(results: dict[str, dict[str, Any]], baseline: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(markdown(results, baseline), encoding="utf-8")
    (out / "comparison.json").write_text(
        json.dumps(compare(results, baseline), indent=2), encoding="utf-8"
    )


def _validate(dataset: EvalDataset) -> int:
    from ..config import Settings
    from ..models import IndexRequest
    from ..reranker import NoopReranker
    from ..service import RetrievalService

    problems = 0
    with tempfile.TemporaryDirectory() as tmp:
        service = RetrievalService(
            Settings(data_dir=Path(tmp), allowed_roots=[c.root for c in dataset.corpora]),
            reranker=NoopReranker(),
        )
        try:
            chunks: list[dict[str, Any]] = []
            for corpus in dataset.corpora:
                service.index(
                    IndexRequest(
                        corpus_id=corpus.corpus_id,
                        root=corpus.root,
                        source_types=corpus.source_types,
                        exclude_globs=corpus.exclude_globs,
                    )
                )
                for path, file in service.store.files(corpus.corpus_id).items():
                    if file.status != "active":
                        continue
                    for record in service.store.chunks_for_path(corpus.corpus_id, path).values():
                        if record.status == "active":
                            chunks.append(
                                {
                                    "chunk_id": record.chunk_id,
                                    "path": record.path,
                                    "symbol": record.symbol,
                                    "section": record.section,
                                }
                            )
        finally:
            service.close()
    for task in dataset.tasks:
        for judgment in task.judgments():
            hits = sum(judgment.matches(c) for c in chunks)
            if hits == 0:
                problems += 1
                print(f"UNRESOLVED {task.id}: {judgment.key()}")
    print(f"{len(dataset.tasks)} tasks, {problems} unresolved judgments")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
