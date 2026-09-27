# Integrating the Retrieval Layer with AHAWR

The Context Retrieval Layer runs **inside the n8n container**. The `ahawr_retrieval` package is
installed into the n8n image and executed by Execute Command nodes, the same way the Run Manager
executes `hermes_compress.py`. There is no extra container or daemon. `rag-platform` stays a
separate, optional external service.

```
n8n container ── Execute Command: python3 -m ahawr_retrieval.cli exec '<base64 request>'
   │                 │  reads  /workspace (read-only mount of the Hermes workspace)
   │                 │  keeps  /home/node/.n8n/ahawr-retrieval/{index,retrieval_logs}.sqlite
   │                 ├─ optional → reranker-service  (RETRIEVAL_RERANKER_URL)
   │                 └─ optional → rag-platform      (RETRIEVAL_RAG_*), local index on failure
   └─ Data Tables: execution state, untouched by retrieval
```

## 1. Build n8n with the retrieval layer

1. Copy `.env.example` to `.env` next to `docker-compose.yml`. Set:
   * `AHAWR_WORKSPACE_DIR` to the directory Hermes works in;
   * the reranker and embedding endpoints;
   * optionally the `RETRIEVAL_RAG_*` values.
2. Rebuild and start:

   ```powershell
   docker compose up -d --build
   docker exec n8n-autonomous-agents python3 -m ahawr_retrieval.cli exec '{"action":"health"}'
   ```

The compose file mounts the workspace read-only at `/workspace`. It stores the index, cache and
retrieval logs in the n8n data volume (`RETRIEVAL_DATA_DIR=/home/node/.n8n/ahawr-retrieval`).

## 2. Indexing

You do not need a manual step. On the first retrieval call the workflow passes
`bootstrap: [{corpus_id, root}]` and the corpus is indexed automatically. Every later call runs an
incremental sync first (`freshness_mode=sync`), so files changed by the Worker are re-indexed chunk
by chunk before the Reviewer's retrieval. Embeddings missing after an interrupted run or an
embedder outage are back-filled on later syncs, with a 60 s back-off while the embedder is down.

For a large repository with a real embedding model, run the first index once by hand to keep it
out of the task's timeout:

```powershell
docker exec n8n-autonomous-agents python3 -m ahawr_retrieval.cli index ahawr-workspace --root /workspace
```

External documentation can be pushed as inline documents with the `index` action:

```json
{"action": "index",
 "request": {"corpus_id": "ahawr-docs",
             "documents": [{"path": "runbooks/deploy.md", "content": "# Deploy …",
                            "version": "2026-09-20", "valid_until": "2027-01-01T00:00:00Z"}],
             "documents_mode": "upsert"}}
```

## 3. Enable it in AHAWR v13

1. Import `AHAWR_v13.json`. It has the same workflow id as v12 and replaces it. Check that
   `Worker Start` / `Reviewer Start` still point at your `Hermes Run Manager v5` workflow id.
2. Add these optional columns to the `hermes_config` Data Table (see `hermes_config.csv`):

   | column | example | meaning |
   |---|---|---|
   | `retrieval_enabled` | `true` | master switch (default: off) |
   | `retrieval_corpora_json` | `ahawr-workspace` | comma-separated corpus ids, or a JSON array of ids / `{"corpus_id","root"}` objects |
   | `retrieval_workspace_root` | `/workspace` | root used to bootstrap id-only corpora |
   | `retrieval_timeout_ms` | `120000` | hard limit per call (`timeout` around the command); on timeout the task runs without context |
   | `retrieval_worker_max_tokens` | `6000` | Worker context budget |
   | `retrieval_reviewer_max_tokens` | `5000` | Reviewer context budget |
   | `retrieval_label` | `hybrid-v1` | configuration label written to retrieval logs for A/B analysis |

3. Optional, per mission: a `retrieval_corpora_json` column in `missions` overrides the corpora.

Execute Command must stay enabled. The compose file already sets `NODES_EXCLUDE=[]`; it is also
required by `hermes_compress.py`.

### What changes in the workflow

```
All Tasks Completed? ─(run task)→ Worker Retrieval Enabled? ─yes→ Build Worker Retrieval Payload → Retrieve Worker Context ─┐
                                                           └─no──────────────────────────────────────────────────────────→ Attach Worker Context → Worker Start
Reviewer State → Reviewer Retrieval Enabled? ─yes→ Build Reviewer Retrieval Payload → Retrieve Reviewer Context ─┐
                                            └─no──────────────────────────────────────────────────────────────→ Attach Reviewer Context → Reviewer Start
```

* `Build * Retrieval Payload` (Code) base64-encodes the request.
* `Retrieve * Context` (Execute Command) runs
  `timeout <s> python3 -m ahawr_retrieval.cli exec '<payload>'; exit 0` with
  `continueRegularOutput`. The CLI always prints one JSON line and exits 0. A timeout, a missing
  package or any error produces **no context**, and the task proceeds as in v12
  (`retrieval_*_status = unavailable`, reason in `retrieval_*_error`).
* `Attach * Context` only adds `retrieval_*` fields to the in-flight item. All Data Table
  writes use explicitly mapped columns, so nothing retrieval-related is persisted to
  `Autonomous Agent Task State` / `Task Attempts`, and resume/recovery paths are unchanged.
* The Worker resume path (`Resume Worker?` → `Worker Start`) continues an existing Hermes session
  and intentionally skips retrieval.
* Worker input gets the context block after `SCOPE` and before `PREVIOUS REVIEW FEEDBACK`;
  Reviewer input gets it after `WORKER RESULT`.
* The request's `trace` carries only `mission_id`, `state_namespace`, `task_id`, `role` and
  `label` — no run, session, attempt or status identifiers.

Each call is a short-lived process: ~0.3 s warm on a small repository, plus index sync time. The
processes share one SQLite index (WAL, 30 s busy timeout). The rag-platform circuit-breaker state
and the embedder back-off are persisted in that index, so they apply across calls.

## 4. rag-platform as external backend

No change to rag-platform is required.

1. In rag-platform (admin API/UI), create a project collection, e.g. `ahawr-code`, and a service
   API key allowed to write and search it. Pick a fixed owner UUID for AHAWR.
2. Set in `.env`: `RETRIEVAL_RAG_URL`, `RETRIEVAL_RAG_API_KEY`, `RETRIEVAL_RAG_OWNER_ID`,
   `RETRIEVAL_RAG_PROJECT_ID`, `RETRIEVAL_RAG_COLLECTION`. Keep `RETRIEVAL_BACKEND=auto`.
3. After the next sync, every active chunk is mirrored into rag-platform as one document. Check
   with `{"action":"health"}` → `backend.rag_platform.mirror.<corpus>.pending_chunks == 0`.

If rag-platform is unavailable, retrieval is served from the local index automatically, and
responses carry `degraded_reasons: ["rag_platform_unavailable:fallback_local"]`.

## 5. Command contract

| action | request |
|---|---|
| `retrieve` | profile (`worker`/`reviewer`), corpora, `task`, `review`, optional `query`, `workspace_state` (`code_snapshot`, `docs_snapshot`, `doc_versions`, `strict`), `freshness_mode` (`trust`/`verify`/`sync`), `budget`, `options` (profile overrides), `cache` (`use`/`refresh`/`bypass`), `trace`, `include_candidates`; plus top-level `bootstrap` |
| `index` | `corpus_id`, `root`, `mode` (`incremental`/`full`), `paths`, `source_types`, `include_globs`, `exclude_globs`, `documents`, `documents_mode`, `force` |
| `invalidate` | `corpus_id` + `paths` / `chunk_ids` / `all`, optional `source_type`, `reason`, `reindex` |
| `health` | — (embedder, reranker, backend and mirror status) |

Result: `{"ok": true, "action": …, "response": {…}}` or
`{"ok": false, "action": …, "error": …, "status_code": 400|404|422|500}`.

The same contract is also served as an optional HTTP API (`POST /retrieve`, `/index`,
`/invalidate`) by `ahawr-retrieval serve`, after `pip install ".[server]"`. It is useful for
development and tooling, and not needed by AHAWR.
