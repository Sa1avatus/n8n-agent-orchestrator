# Eval Harness

The harness exists so that every retrieval change — a new retriever, weights, a reranker, a
dependency graph, historical evidence, LTR — is accepted or rejected on data, on the same
tasks, against a recorded baseline.

## Datasets

Both levels share one schema (`ahawr_retrieval.eval.datasets.EvalDataset`).

* **Gold** — small, manually verified. Judgments name relevant units by `path` plus optional
  `symbol` (a class judgment also matches its methods), `section` (substring of the heading
  breadcrumb) or exact `chunk_id`, with grades 1–3. Labels therefore survive re-chunking.
  Tasks may carry retry `variants` (`expect: reuse | reretrieve`) to test cache decisions.
  `retrieval-service/eval/datasets/gold/ahawr-gold-v1.json` is a seed set over this
  repository, verified against commit-pinned content; extend it with real AHAWR tasks.
* **Silver** — weak ground truth from AHAWR history, generated with `build-silver` from exports
  of `Autonomous Agent Task Attempts` and `Autonomous Agent Task State`: tasks with a Reviewer
  `pass`, file-level judgments from an explicit changes map (e.g. `git diff --name-only` per
  task) or from paths in the accepted Worker output and task scope. Every silver task is
  `weak: true`; aggregates report `strong_only` separately. A Reviewer pass is observed
  evidence, not proven correctness.

Validate labels after repository changes: `ahawr-retrieval-eval validate-dataset --dataset …`.

## Configurations

`retrieval-service/eval/configs/*.json` — each is a name, profile `options`, an embedder spec
(`hashing`, `local` for the built-in CPU model, or `openai`) and optionally `reranker_url`,
`reranker_model` (built-in CPU cross-encoder) / `rag` settings. `${VAR}` and
`${VAR:-default}` are resolved from the environment so secrets stay out of files.
Configurations sharing embedder and backend are evaluated on one byte-identical index.

## Metrics

Retrieval (per task, averaged; also per profile and file-level `file_*`):
Recall@K, Precision@K (chunk-level), MRR, nDCG@K (graded, each judgment counted once),
ContextRecall (relevant units present in the budgeted context).

System: retrieval latency mean/p50/p95, reranker latency, context tokens, degraded rate,
cache hit ratio and cache decision accuracy on retry variants, retrieval calls per task.

AHAWR-level (from Data Table exports, per `label`): first-pass rate, retry rate, average
retries per task, final task success rate, total task latency; joined with retrieval logs for
retrieval calls per task, cache hit ratio, retrieval/reranker latency and context tokens.

## Commands

```bash
pip install ./retrieval-service
cd retrieval-service

# identical tasks, several configurations, baseline comparison
ahawr-retrieval-eval run --dataset eval/datasets/gold/ahawr-gold-v1.json \
  --config eval/configs/baseline-lexical.json --config eval/configs/hybrid.json \
  --config eval/configs/hybrid-rerank.json --baseline baseline-lexical --out eval/runs/2026-09-27

# silver set from AHAWR history
ahawr-retrieval-eval build-silver --attempts attempts.json --state task_state.json \
  --root /path/to/workspace --corpus-id ahawr-workspace --changes changes.json \
  --out eval/datasets/silver/ahawr-silver.json

# AHAWR outcome metrics per configuration label
ahawr-retrieval-eval ahawr-metrics --attempts attempts.json \
  --label-map labels.json --retrieval-log /data/retrieval_logs.sqlite

# features for later LTR
ahawr-retrieval export-logs --out features.jsonl
```

`hybrid-rerank` needs `RETRIEVAL_RERANKER_URL` pointing at a running `reranker-service`;
without it the configuration runs unreranked and reports the degradation.
`hybrid-local-rerank` and `hybrid-symbol-e5-local` use the models baked into the container image;
run them inside it (`RETRIEVAL_RERANKER_MODEL_DIR=/models`). `ahawr-gold-ru` repeats the gold
tasks in Russian. Results for both: `eval/results/2026-09-28-local-models.md`.

## Decision rule

A change becomes the default only if, against the current default on the gold set:
MRR and Recall@5 do not decrease, at least one of them improves, ContextRecall does not
decrease, silver `file_Recall@10` does not regress, cache decision accuracy stays 1.0, and
p95 latency stays well inside `retrieval_timeout_ms`. AHAWR-level A/B (first-pass rate,
retries, success) is the final confirmation before enabling a phase by default.
