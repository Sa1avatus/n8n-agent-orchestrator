# Integrating the Retrieval Layer with AHAWR

## 1. Run the service

```powershell
cd retrieval-service
Copy-Item .env.example .env        # set AHAWR_WORKSPACE_DIR, reranker URL, optional API key
docker compose up -d --build
Invoke-RestMethod http://localhost:8500/health
```

`AHAWR_WORKSPACE_DIR` must be the same directory Hermes works in; it is mounted read-only at
`/workspace`. Without Docker: `pip install ./retrieval-service` and
`ahawr-retrieval serve --port 8500` with `RETRIEVAL_ALLOWED_ROOTS` pointing at the workspace.

## 2. Index the workspace

```powershell
$body = @{ corpus_id = 'ahawr-workspace'; root = '/workspace' } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://localhost:8500/index -ContentType 'application/json' -Body $body
```

Subsequent calls are incremental. `/retrieve` also syncs before searching (default
`freshness_mode=sync`), so files changed by the Worker are re-indexed chunk by chunk before the
Reviewer's retrieval. External documentation can be pushed inline:

```json
POST /index
{"corpus_id": "ahawr-docs",
 "documents": [{"path": "runbooks/deploy.md", "content": "# Deploy …", "version": "2026-09-20",
                "valid_until": "2027-01-01T00:00:00Z", "source_url": "https://…"}],
 "documents_mode": "upsert"}
```

## 3. Enable it in n8n (AHAWR v13)

1. Import `AHAWR_v13.json` (same workflow id as v12; it replaces it). Check that `Worker Start` /
   `Reviewer Start` still point at your `Hermes Run Manager v5` workflow id.
2. Add these optional columns to the `hermes_config` Data Table (see `hermes_config.csv`):

   | column | example | meaning |
   |---|---|---|
   | `retrieval_enabled` | `true` | master switch (default: off) |
   | `retrieval_url` | `http://host.docker.internal:8500` | service base URL |
   | `retrieval_corpora_json` | `["ahawr-workspace","ahawr-docs"]` | corpora to search |
   | `retrieval_timeout_ms` | `60000` | HTTP timeout; on timeout the task runs without context |
   | `retrieval_worker_max_tokens` | `6000` | Worker context budget |
   | `retrieval_reviewer_max_tokens` | `5000` | Reviewer context budget |
   | `retrieval_label` | `hybrid-v1` | configuration label written to retrieval logs for A/B analysis |

3. Optional per mission: a `retrieval_corpora_json` column in `missions` overrides the corpora.
4. If `RETRIEVAL_API_KEY` is set, open `Retrieve Worker Context` and `Retrieve Reviewer Context`
   and set *Authentication → Generic → Header Auth* with `Authorization: Bearer <key>` stored
   as an n8n credential (never in the Data Table).

### What changes in the workflow

```
All Tasks Completed? ─(run task)→ Worker Retrieval Enabled? ─yes→ Retrieve Worker Context ─┐
                                                           └─no──────────────────────────→ Attach Worker Context → Worker Start
Reviewer State → Reviewer Retrieval Enabled? ─yes→ Retrieve Reviewer Context ─┐
                                            └─no───────────────────────────→ Attach Reviewer Context → Reviewer Start
```

* The HTTP nodes use `neverError` + `continueRegularOutput`: an unreachable service, HTTP error
  or timeout produces **no context** and the task proceeds as in v12.
* `Attach * Context` only adds `retrieval_*` fields to the in-flight item. All Data Table
  writes use explicitly mapped columns, so nothing retrieval-related is persisted to
  `Autonomous Agent Task State` / `Task Attempts`, and resume/recovery paths are unchanged.
* The resume path (`Resume Worker?` → `Worker Start`) continues an existing Hermes session and
  intentionally skips retrieval.
* Worker input gets the context block after `SCOPE` and before `PREVIOUS REVIEW FEEDBACK`;
  Reviewer input gets it after `WORKER RESULT`.
* `trace` sends only `mission_id`, `state_namespace`, `task_id`, `role` and `label` — no run,
  session, attempt or status identifiers.

## 4. Optional: rag-platform as external backend

No change to rag-platform is required.

1. In rag-platform (admin API/UI): create a project collection, e.g. `ahawr-code`, and a
   service API key allowed to write and search it; choose a fixed owner UUID for AHAWR.
2. Set in `retrieval-service/.env`:
   `RETRIEVAL_RAG_URL`, `RETRIEVAL_RAG_API_KEY`, `RETRIEVAL_RAG_OWNER_ID`,
   `RETRIEVAL_RAG_PROJECT_ID`, `RETRIEVAL_RAG_COLLECTION` (and keep `RETRIEVAL_BACKEND=auto`).
3. Re-run `/index`: every active chunk is mirrored as one rag-platform document. Check
   `GET /health` → `backend.rag_platform.mirror.<corpus>.pending_chunks == 0`.

If rag-platform is unavailable, retrieval is served from the local index automatically and
responses carry `degraded_reasons: ["rag_platform_unavailable:fallback_local"]`.

## 5. API summary

| Endpoint | Purpose |
|---|---|
| `POST /retrieve` | profile (`worker`/`reviewer`), corpora, `task`, `review`, optional `query`, `workspace_state` (`code_snapshot`, `docs_snapshot`, `doc_versions`, `strict`), `freshness_mode` (`trust`/`verify`/`sync`), `budget`, `options` (profile overrides), `cache` (`use`/`refresh`/`bypass`), `trace`, `include_candidates` |
| `POST /index` | `corpus_id`, `root`, `mode` (`incremental`/`full`), `paths`, `source_types`, `include_globs`, `exclude_globs`, `documents`, `documents_mode`, `force` |
| `POST /invalidate` | `corpus_id` + `paths` / `chunk_ids` / `all`, optional `source_type`, `reason`, `reindex` |
| `GET /corpora`, `GET /corpora/{id}` | snapshots, generations, chunk/file counts by status |
| `GET /health` | embedder, reranker, backend and mirror status |

The FastAPI schema is available at `/docs` and `/openapi.json`.
