# Context Retrieval Layer — Roadmap

Principles for every phase:

* **One phase at a time**, each shippable on its own; no big-bang refactor.
* **Evidence first.** A phase starts only when the Eval Harness shows the current phase is
  stable, and it is enabled by default only when the harness shows an improvement on the gold
  set (and no regression on the silver set) with acceptable latency/token cost.
* **Backward compatible with AHAWR.** No change to Data Table schemas, execution state,
  recovery paths or mission format. New workflow behaviour is behind optional configuration
  columns; with retrieval disabled or unreachable AHAWR behaves exactly like v12.
* **Historical evidence is never authoritative.** Reviewer `pass` is observed evidence.
  Current code, current documentation, acceptance criteria and deterministic validation always
  outrank it.

| Phase | Scope | Status | Gate to start | Gate to enable by default |
|---|---|---|---|---|
| 1 | Docs + code lexical/vector retrieval, symbol search, RRF, text-only reranking, deterministic filters/ranking, provenance, snapshots, cache, retrieval logging, Eval Harness, n8n integration | **Delivered (MVP)** | — | Gold-set Recall@5 / MRR above lexical baseline; p95 latency within n8n timeout |
| 2 | Incremental indexing, chunk-level invalidation, semantic query fingerprint + cache reuse, rag-platform backend with fallback | **Mechanisms delivered**; semantic reuse on by default with conservative thresholds | Phase 1 metrics recorded | Cache decision accuracy = 1.0 on gold variants (no false reuse); AHAWR retry rate not worse than retrieval-off |
| 3 | Dependency/structural retrieval: tree-sitter parsing, import/call graph, graph expansion of candidates | Planned | ≥ 30 gold tasks; logged misses show relevant code reachable only via imports/calls | Recall@10 gain on structural tasks, no Precision@5 regression |
| 4 | Historical execution evidence (accepted solutions, review feedback) as a low-priority `history` source | Planned; `history` source type, authority label, ×0.5 score cap and ≤20 % budget share already exist | Silver set built; phase 3 decided | First-pass rate / retries improve in AHAWR A/B; no evidence outranks current code in audits |
| 5 | Downstream outcome feedback loop: follow accepted decisions (later reverts, re-opened tasks, failing checks) and down-weight evidence with bad outcomes | Planned | Phase 4 running; outcome signals available | Outcome-adjusted evidence improves AHAWR metrics |
| 6 | Learning-to-Rank over logged features | Planned; every feature is already logged per candidate | Enough judgments (target ≥ 1 000 graded query–chunk pairs from gold + reviewed logs) | Offline nDCG@10 gain over hand-set weights with confidence interval excluding 0; online AHAWR metrics not worse |

## Phase details

### Phase 1 — delivered

`retrieval-service/` as the `ahawr-retrieval` container of the n8n compose stack with
`/retrieve`, `/index`, `/invalidate`; Worker and Reviewer profiles;
FTS5 BM25 + vector + symbol retrieval; weighted RRF; text-only cross-encoder via
`reranker-service`; deterministic hard filters and ranking layer; chunk provenance with code
and docs versioning; retrieval cache; per-candidate feature logging; Eval Harness with gold
and silver datasets and AHAWR-level metrics; `AHAWR_v13.json` fail-open integration.

### Phase 2 — mechanisms delivered, rollout gated by evaluation

* incremental sync (stat walk, chunk-level diff, embedding reuse by content hash);
* chunk/path invalidation with per-source-type generations;
* component-wise semantic query fingerprint and cache revalidation across unrelated changes;
* optional `rag-platform` backend (mirror + search + validation + circuit-breaker fallback).

Rollout: run AHAWR missions with `retrieval_label` values (`off`, `hybrid-v1`,
`hybrid-v1-rerank`) and compare with `ahawr-retrieval-eval ahawr-metrics`. Tighten
`cache.jaccard_threshold` or disable `cache.semantic_reuse` per profile if false reuse appears.
For a live-task A/B (RAG on vs. RAG off on one mission) use the procedure in
[`EVALUATION.md`](EVALUATION.md) §"Live-task A/B procedure" and cross-check with the usage
metric CLI (`ahawr-retrieval usage --runner-url … --since …`).

### Phase 3 — dependency / structural retrieval

Add a `graph` retriever behind the existing candidate-list interface
(`RetrievalService._candidate_lists`) and a new chunker version (`CHUNKER_VERSION`) that uses
tree-sitter for symbol boundaries. Symbol edges live in the local store keyed by `chunk_id`,
so chunk-level invalidation keeps working. Enable per profile via `options` for evaluation.

### Phase 4 — historical execution evidence

Index accepted Worker outputs and Reviewer reasons as `source_type=history` documents in a
separate corpus built from the exported attempt log (read-only export; never the live state
tables). Profiles opt in through `source_types`. Rendering labels them
`historical_evidence`, the ranking layer multiplies their score by `history_multiplier`, and
context assembly caps their share of the budget.

### Phase 5 — downstream outcome feedback

Record later outcomes of accepted tasks (revert, re-opened task, failing validation) against the
evidence chunks that supported them and expose them as a feature/penalty. Outcomes are
analytics data, not execution state.

### Phase 6 — Learning-to-Rank

Train on the logged features (`ahawr-retrieval export-logs`) joined with gold judgments and
reviewed log samples; ship as an optional ranking layer keyed by `config_id`, evaluated
against the hand-set weights with the same harness.

## Compatibility checklist for every phase

- [ ] `AHAWR_v*.json` with retrieval disabled produces the same Worker/Reviewer inputs as before.
- [ ] No new columns written to `Autonomous Agent Task State` / `Task Attempts`.
- [ ] `/retrieve` request and response remain backward compatible (new fields optional).
- [ ] Store migrations are additive (`CREATE … IF NOT EXISTS`); `CHUNKER_VERSION` bump triggers
      automatic re-chunking with embedding reuse.
- [ ] Eval report committed under `retrieval-service/eval/results/` comparing against the
      previous default configuration.
