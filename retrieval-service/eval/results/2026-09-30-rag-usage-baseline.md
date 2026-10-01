# Baseline gold metrics and latency on a frozen snapshot, 2026-09-30

Follows the procedure from `2026-09-28-local-models.md`: index a frozen
`git archive HEAD` copy of the repository, run the offline gold evals, and record
ranking metrics, ContextRecall and per-query latency.

## Setup

* Snapshot commit: `84ce89eb2f442e605c503c2ce48dda7a5c08bc20` (HEAD).
  `git archive HEAD | tar -x -C /tmp/ahawr-snapshot` — 132 files; the indexed
  tree is `tree:4ae7c03ef84a02a5`.
* Eval command (`ahawr-retrieval-eval run`), one config per run:

```
python -m ahawr_retrieval.eval.cli run \
  --dataset <snapshot>/retrieval-service/eval/datasets/gold/ahawr-gold-v1.json \
  --config  <snapshot>/retrieval-service/eval/configs/hybrid-symbol-e5-local.json \
  --out /tmp/ahawr-eval-gold-v1
python -m ahawr_retrieval.eval.cli run \
  --dataset <snapshot>/retrieval-service/eval/datasets/gold/ahawr-gold-ru.json \
  --config  <snapshot>/retrieval-service/eval/configs/hybrid-symbol-e5-local.json \
  --out /tmp/ahawr-eval-gold-ru
```

* Config used: `hybrid-symbol-e5-local` — BM25 + vectors + symbol/path search,
  RRF, deterministic ranking, **no reranker** (`rerank.enabled=false`), embedder
  `intfloat/multilingual-e5-small` (ONNX via fastembed, `query:`/`passage:`
  prefixes, CPU, onnxruntime, 4 threads). This is the production-like
  `RETRIEVAL_EMBEDDER=local` + `RETRIEVAL_RERANKER=none` path.
* Models: the e5-small ONNX was the only locally-available model (it was not
  pre-cached, so it was downloaded once to the fastembed cache before the run;
  the reranker cross-encoders are not cached and are not used here). All other
  runs (BM25/hash, no-embedder) use no external model.

## Results

| metric | gold v1 | gold-ru |
|---|---|---|
| nDCG@5 | 0.541 | 0.480 |
| MRR | 0.847 | 0.778 |
| ContextRecall | 0.661 | 0.562 |
| Retrieval latency mean | 79.0 ms | 75.1 ms |
| Retrieval latency p50 | 77.5 ms | 78.0 ms |
| Retrieval latency p95 | 120 ms | 113 ms |
| Context tokens (mean) | 3986 | 3500 |

### Per-query retrieval latency (ms, ascending)

* gold v1: 48, 52, 56, 59, 64, 66, 67, 69, 77, 78, 86, 98, 100, 100, 100, 104, 120
* gold-ru: 51, 51, 52, 54, 58, 64, 64, 66, 78, 80, 83, 84, 84, 94, 95, 107, 113

### Per-task (MRR / Recall@5)

| task | gold v1 | gold-ru |
|---|---|---|
| G01 | 1.00 / 0.50 | 1.00 / 0.50 |
| G02 | 0.50 / 0.75 | 1.00 / 0.75 |
| G03 | 1.00 / 0.75 | 0.50 / 0.25 |
| G04 | 1.00 / 0.71 | 1.00 / 0.57 |
| G05 | 1.00 / 0.50 | 1.00 / 0.50 |
| G06 | 1.00 / 0.50 | 0.50 / 0.25 |
| G07 | 1.00 / 0.60 | 1.00 / 0.60 |
| G08 | 1.00 / 0.20 | 1.00 / 0.20 |
| G09 | 1.00 / 0.50 | 1.00 / 0.25 |
| G10 | 1.00 / 0.60 | 1.00 / 0.60 |
| G11 | 1.00 / 0.75 | 1.00 / 0.75 |
| G12 | 1.00 / 0.67 | 1.00 / 0.67 |
| G13 | 1.00 / 0.25 | 0.33 / 0.25 |
| G14 | 0.50 / 0.50 | 0.50 / 0.25 |
| G15 | 0.07 / 0.00 | 0.07 / 0.00 |
| G16 | 0.33 / 0.67 | 0.33 / 0.33 |
| G17 | 1.00 / 0.50 | 1.00 / 0.50 |

## Consistency with the 2026-09-28 results

The 2026-09-28 table (same `hybrid-symbol`, e5-small, no reranker) reported
gold v1 nDCG@5 **0.580** and gold-ru nDCG@5 **0.505**. This frozen-snapshot run
gives gold v1 **0.541** and gold-ru **0.480**. The ranking is deterministic and
fully reproducible (a re-run on the same snapshot returned identical
nDCG@5/MRR/ContextRecall, delta 0.000), so the difference is not run-to-run
noise: it is the corpus. The 2026-09-28 run was measured on commit `35dee91`
(this snapshot is `84ce89e`, which is newer and has since been edited — e.g.
`retrieval-service`, `claude-runner`, `docs/retrieval`), so the indexed tree is
different and a few gold judgments resolve to different chunks. The gap
(~0.04 on gold v1, ~0.025 on gold-ru) is the expected shift from the changed
corpus, on top of the ~0.015 same-corpus run-to-run variation that the
2026-09-28 notes already attribute to the baseline itself.
