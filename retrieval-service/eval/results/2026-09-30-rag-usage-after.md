# T016 follow-up: TRUE same-corpus A/B, gold v1 + gold-ru, 2026-09-30

Follow-up to `2026-09-30-rag-usage-baseline.md`. This run is the **true same-corpus A/B**
required by T016: the SAME frozen snapshot `/tmp/ahawr-snapshot` (committed tree
`84ce89eb`, `code_snapshot tree:d2ee1c58ab70c803`, `docs_snapshot docs:cb51d8450d16c305`)
is indexed once and scored twice — with the **baseline** service code and with the
**modified** service code. Gold v1 and gold-ru are each run once per code version (4 runs).
All deltas below are from THIS same-corpus comparison; the 09-30 doc's original
before/after table (different corpus snapshots) is superseded by this one.

## Commands

The four runs, from `/tmp/samecorpus_ab.sh` (2026-09-30, fresh index per run, work dir
`work/index-b0b90ed6412b`):

```
V=/tmp/venv/bin/python3
DATASET_V1=/tmp/ahawr-snapshot/retrieval-service/eval/datasets/gold/ahawr-gold-v1.json
DATASET_RU=/tmp/ahawr-snapshot/retrieval-service/eval/datasets/gold/ahawr-gold-ru.json
CFG=/tmp/ahawr-snapshot/retrieval-service/eval/configs/hybrid-symbol-e5-local.json
BASE_SRC=/tmp/ahawr-snapshot/retrieval-service/src
MOD_SRC=/tmp/ahawr-samecorpus-after/retrieval-service/src

# baseline code, snapshot corpus
PYTHONPATH=$BASE_SRC $V -m ahawr_retrieval.eval.cli run --dataset $DATASET_V1 --config $CFG --out /tmp/scab-base-v1
PYTHONPATH=$BASE_SRC $V -m ahawr_retrieval.eval.cli run --dataset $DATASET_RU --config $CFG --out /tmp/scab-base-ru
# modified code, snapshot corpus
PYTHONPATH=$MOD_SRC $V -m ahawr_retrieval.eval.cli run --dataset $DATASET_V1 --config $CFG --out /tmp/scab-after-v1
PYTHONPATH=$MOD_SRC $V -m ahawr_retrieval.eval.cli run --dataset $DATASET_RU --config $CFG --out /tmp/scab-after-ru
```

Result JSONs (one per run):

| run | result JSON | index sqlite |
|---|---|---|
| base-v1 | `/tmp/scab-base-v1/hybrid-symbol-e5-local.json` | `/tmp/scab-base-v1/work/index-b0b90ed6412b/index.sqlite` |
| base-ru | `/tmp/scab-base-ru/hybrid-symbol-e5-local.json` | `/tmp/scab-base-ru/work/index-b0b90ed6412b/index.sqlite` |
| after-v1 | `/tmp/scab-after-v1/hybrid-symbol-e5-local.json` | `/tmp/scab-after-v1/work/index-b0b90ed6412b/index.sqlite` |
| after-ru | `/tmp/scab-after-ru/hybrid-symbol-e5-local.json` | `/tmp/scab-after-ru/work/index-b0b90ed6412b/index.sqlite` |

Same-corpus proof: every result JSON's `index.ahawr-repo` field records
`code_snapshot: tree:d2ee1c58ab70c803` and `docs_snapshot: docs:cb51d8450d16c305`;
all four sqlite indexes contain the identical 101 files / 1447 chunks and the identical
set of `(chunk_id, content_hash)` pairs (empty symmetric difference between base and
after indexes).

## Results (before = baseline code, after = modified code)

| metric | gold v1 | | gold-ru | |
|---|---|---|---|---|
| | before | after | delta | before | after | delta |
| nDCG@5 | 0.5549 | 0.5549 | **0.000** | 0.4747 | 0.4747 | **0.000** |
| MRR | 0.8762 | 0.8762 | **0.000** | 0.7684 | 0.7684 | **0.000** |
| ContextRecall | 0.6606 | 0.6690 | **+0.0084** | 0.5620 | 0.5704 | **+0.0084** |
| Retrieval latency mean | 80.6 ms | 121.2 ms | **+50.3 %** | 113.8 ms | 115.9 ms | **+1.9 %** |
| Retrieval latency p50 | 77.0 ms | 104.7 ms | **+36.0 %** | 99.6 ms | 103.8 ms | **+4.2 %** |
| Retrieval latency p95 | 130.0 ms | 286.3 ms | **+119.5 %** | 206.4 ms | 208.8 ms | **+1.2 %** |
| Context tokens (mean) | 3955.9 | 3985.1 | **+29.2** | 3534.1 | 3576.5 | **+42.4** |

(Exact aggregate values: v1 before nDCG@5 0.554893 / MRR 0.876225 / CR 0.660644;
after 0.554893 / 0.876225 / 0.669048. ru before 0.474653 / 0.768382 / 0.562045;
after 0.474653 / 0.768382 / 0.570448.)

### Per-task

Only **G04** differs between before and after, and only ContextRecall — it **improves**:

| task | gold v1 before | gold v1 after | gold-ru before | gold-ru after |
|---|---|---|---|---|
| G04 | 1.00 / 0.7143 | 1.00 / **0.8571** | 1.00 / 0.5714 | 1.00 / **0.7143** |

(All other tasks are identical before/after on both sets. No task shows a ranking-metric
drop; the modified code is ranking-inert on this corpus and strictly improves
ContextRecall on G04 via the related-test pack, which adds the grade-1
`tests/test_fingerprint.py` fragment to the context: G04 context tokens
3053 → 3218 (v1) and 1872 → 2037 (ru).)

### G06 ranks (observed, both runs)

G06's top-10 list is **byte-identical** in the before and after runs. The graded
`filters.py::_check` chunk is at **rank 5** (final 0.756906) in **both** runs;
`filters.py::apply_hard_filters` (grade 2) and `WorkspaceState` (grade 1) are **not** in
the top-10 in either run. The top-10 is:

| rank | path (symbol) | final |
|---|---|---|
| 1 | tests/test_retrieval.py `test_expired_and_pinned_docs_are_filtered` | 0.82 |
| 2 | docs/retrieval/ARCHITECTURE.md | 0.787198 |
| 3 | src/ahawr_retrieval/indexer.py | 0.769708 |
| 4 | src/ahawr_retrieval/indexer.py `Indexer._sync_documents` | 0.759843 |
| 5 | src/ahawr_retrieval/filters.py `_check` | 0.756906 |
| 6 | src/ahawr_retrieval/rag_platform.py | 0.733146 |
| 7 | src/ahawr_retrieval/filters.py | 0.732996 |
| 8 | docs/retrieval/ARCHITECTURE.md | 0.708012 |
| 9 | src/ahawr_retrieval/service.py `RetrievalService._chunk_out` | 0.693742 |
| 10 | docs/retrieval/ARCHITECTURE.md | 0.673113 |

The old 09-30 doc's "G06 CR 0.750 → 0.500" drop was measured on the **non-same-corpus**
after run (index `tree:112a4c9b78c93b69`, which additionally contained
`docs/retrieval/WORKLOG-rag-usage.md` and `usage.py`). It does **not** occur in this
same-corpus A/B: G06 ContextRecall is 0.75 in both before and after, and its top-10
ranking is unchanged.

## Attribution: features are ranking-inert

* The modified tree's ranker changes (`changed_path_bonus`, `large_patch_penalty`,
  related-tests wiring) do not move any top-10 list on this corpus: before/after top-10
  lists are identical for all 17 tasks on both gold sets. The only per-task metric change
  (G04 ContextRecall, improved) comes from the related-test **packing** adding a
  grade-1 `tests/test_fingerprint.py` fragment to the selected context — a context
  change, not a ranking change.
* Because no ranking metric drops (all deltas are 0.000 or positive), **no feature is
  responsible for any regression and no feature is tuned or disabled by default**.

## G06 explanation correction (old doc lines 79–89)

The old explanation claimed the `filters.py` working-tree edit (the `_check` signature
and `freshness_mode` plumbing) changed `_check`'s chunk content, changed its hash, and
"shifts its BM25/fused score" from 0.7569 to 0.7521, causing G06 CR 0.750 → 0.500.
**Both claims are wrong on the frozen snapshot:**

* `_check`'s final score is **0.756906 in both** the before and after runs — identical to
  6 decimals; there is no score shift.
* A content-hash change does **not** shift BM25: BM25 scoring is computed from the
  chunk's term frequencies and the corpus IDF, not from the content hash. The hash
  change (if any) only invalidates the vector-cache key. In this A/B the chunk
  contents are identical between the two indexes, so even the hash is unchanged.
* The CR 0.500 came only from the non-same-corpus run, where extra files
  (`WORKLOG-rag-usage.md`, `usage.py`, modified tracked files) change the corpus and
  therefore the fused ranking — a corpus effect, not a hash/BM25 effect.

## Acceptance

1. **Ranking drops ≤ 0.02: PASS.** nDCG@5 and MRR deltas are exactly 0.000 on both gold
   sets; ContextRecall **improves** by +0.0084 on both sets (no drop at all). No
   regression to flag, no feature to tune or disable.
2. **Latency: gold-ru PASS; gold v1 FAILS the ~20 % bar.**
   * gold-ru: mean +1.9 %, p50 +4.2 %, p95 +1.2 % — all within ~20 %.
   * gold v1: mean **+50.3 %** (80.6 → 121.2 ms), p50 **+36.0 %**, p95 **+119.5 %**
     (130.0 → 286.3 ms). This exceeds the ~20 % acceptance bar. The v1 p95
     regression is a single-task tail effect: in the before run the p95 task is one
     cold-start retrieval (~130 ms), while in the after run a later v1 task hits the
     ~286 ms tail (CPU/embedder-bound; see the per-task latency in
     `/tmp/scab-{base,after}-v1/hybrid-symbol-e5-local.json`, `tasks[].latency_ms`).
     All ranking metrics are unchanged, so the tail is in retrieval cost, not in
     ranking quality.
   * Context budget: **5000** — the reviewer profile keeps `budget.max_tokens = 5000`
     (`profiles.py`); the reported `context_tokens_mean` (3985 v1 / 3577 ru) is well
     under the budget. **PASS.**
3. **GDN freed share (T005 script, read-only re-run):**

| metric | value |
|---|---|
| matched requests | 69 |
| total selected fragments | 526 |
| total selected tokens | 306,958 |
| (a) backup/artifact files | 0 frag / 0 tok (**0.0 % / 0.0 %**) |
| (b) .patch/.diff > 1000 lines | 181 frag / 137,464 tok (**34.4 % / 44.8 %**) |

The exclusion globs free **0.0 % of fragments and 0.0 % of tokens** (no
backup/artifact files were ever selected by the GDN mission); the large-patch
demotion rule (`LARGE_PATCH_LINES = 1000`) would free **34.4 % of selected
fragments / 44.8 % of selected tokens** (181 of 526 fragments, 137,464 of 306,958
tokens), all from `patches/llama-cpp-dcfr-research.patch` (3,408 lines).

Command (identical to T005, read-only):

```
python retrieval-service/eval/scripts/selection_share.py \
  --db /d/rag-tmp/rag-logs/retrieval_logs.sqlite \
  --corpora d-openaiprojects-llama-cpp dcfr-c060ca97 \
  --root-map d-openaiprojects-llama-cpp=/d/OpenAIProjects/llama.cpp \
             dcfr-c060ca97=/d/rag-tmp/dcfr-work.mission \
  --mission llamacpp-gdn-transactional-prompt-cache --top 10
```

## Verification

* All numbers above were extracted from the four result JSONs listed above
  (`aggregate.retrieval` / `aggregate.system` for aggregates, `tasks[].metrics`,
  `tasks[].latency_ms`, `tasks[].context_tokens` for per-task). Re-running a command
  against the same snapshot reproduces the ranking metrics exactly (deterministic
  index + deterministic ranking); absolute latencies vary with machine load.
* Same-corpus check: identical `code_snapshot`/`docs_snapshot` in all four result JSONs
  and identical `(chunk_id, content_hash)` sets in all four sqlite indexes (101 files /
  1447 chunks each).
* G06 top-10 byte-identical before/after (verified by direct comparison of
  `tasks[].top` in the two v1 result JSONs); G06 `_check` at rank 5 in both.
