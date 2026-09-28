# Built-in CPU models — gold v1 and gold-ru, 2026-09-28

Hand-written summary of `ahawr-retrieval-eval run` results. All models ran in the
`ahawr-retrieval` image on the CPU (onnxruntime, no CUDA), offline (`--network none`), with
4 onnxruntime threads.

* `ahawr-gold-v1`: 17 English tasks over this repository.
* `ahawr-gold-ru`: the same tasks and judgments, titles/objectives/criteria in Russian
  (G10 and G15 were already Russian), as AHAWR missions are often phrased.

## Rerankers (cross-encoders), gold v1, baseline `hybrid-symbol` (no reranker)

| metric | no reranker | jina-v1-tiny-en | jina-v1-turbo-en | ms-marco-MiniLM-L-6 |
|---|---|---|---|---|
| nDCG@5 | 0.603 | 0.530 | 0.515 | 0.400 |
| MRR | 0.941 | 0.868 | 0.897 | 0.833 |
| Recall@10 | 0.694 | 0.682 | 0.663 | 0.645 |
| reranker latency, mean | – | 4.3 s | 6.9 s | 5.2 s |

On `ahawr-gold-ru`, jina-v1-tiny-en: nDCG@5 0.449 → 0.323, MRR 0.660 → 0.507.

**Decision.** No small CPU cross-encoder beats the deterministic ranking without a reranker, and
each adds seconds per request. The built-in reranker stays in the image but is opt-in
(`RETRIEVAL_RERANKER=local`); `auto` uses `reranker-service` when configured and otherwise does
not rerank.

## Embedders, baseline `hybrid-symbol` (hashing)

| metric | gold v1: hashing | gold v1: e5-small | gold-ru: hashing | gold-ru: e5-small |
|---|---|---|---|---|
| nDCG@5 | 0.602 | 0.580 | 0.449 | **0.505** |
| nDCG@10 | 0.652 | 0.615 | 0.487 | **0.555** |
| MRR | 0.941 | 0.902 | 0.660 | **0.824** |
| Recall@10 | 0.694 | 0.659 | 0.530 | **0.619** |
| ContextRecall | 0.723 | 0.694 | 0.541 | **0.666** |
| retrieval latency, mean | 306 ms | 394 ms | 314 ms | 317 ms |

`e5-small` is `intfloat/multilingual-e5-small` (ONNX from `Xenova/multilingual-e5-small`) with
`query: ` / `passage: ` prefixes, `RETRIEVAL_EMBEDDER=local`.

**Reading.** On English tasks e5-small is slightly below hashing (within ~0.02–0.04; the
baseline itself moved by ~0.015 between identical runs). On Russian wording it is clearly better
(MRR +0.16, ContextRecall +0.12), because hashing cannot match Russian task text to English code.
Latency is nearly unchanged; indexing a corpus embeds every chunk once on the CPU.

## per_path_limit 3 → 8

gold v1, hashing, no reranker: ranking metrics identical, ContextRecall 0.719 → 0.709.
On the live JSA task T001 (one large `app/api/main.py`), the limit of 3 dropped
`discover_greenhouse_vacancies`, the three client dependencies and `build_user_model_providers`;
with 8 the context holds 10 of the 11 needed chunks.
