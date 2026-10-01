# Integrating the Retrieval Layer with AHAWR

The Context Retrieval Layer runs in its own container, `ahawr-retrieval`, **inside the n8n
compose stack** (`docker-compose.yml` in the repository root). It is started, stopped and
rebuilt together with n8n, and it is reachable only from the stack network at
`http://ahawr-retrieval:8500`; no port is published to the host. `rag-platform` stays a
separate, optional external service.

```
docker compose stack
├── n8n-autonomous-agents ── HTTP POST http://ahawr-retrieval:8500/retrieve (fail-open)
└── ahawr-retrieval
      reads  /workspace         (Hermes workspace, read-only bind mount)
      keeps  /data              (named volume: index, cache, retrieval logs)
      ├─ built-in → CPU models in /models (RETRIEVAL_EMBEDDER=local, RETRIEVAL_RERANKER=local)
      ├─ optional → reranker-service  (RETRIEVAL_RERANKER_URL, e.g. host.docker.internal:8200)
      └─ optional → rag-platform      (RETRIEVAL_RAG_*), local index on failure
n8n Data Tables: execution state, untouched by retrieval
```

## 1. Start the stack

1. Copy `.env.example` to `.env` next to `docker-compose.yml`. Set:
   * `AHAWR_WORKSPACE_DIR` to the directory Hermes works in;
   * the reranker and embedding endpoints;
   * optionally the `RETRIEVAL_RAG_*` values.
2. Build and start both containers:

   ```powershell
   docker compose up -d --build
   docker compose ps                     # n8n-autonomous-agents, ahawr-retrieval
   docker compose exec ahawr-retrieval python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8500/health').read().decode())"
   ```

`n8n` declares an optional dependency on `ahawr-retrieval`, so n8n still starts if the retrieval
container is missing. AHAWR then simply runs without context.

## 2. Indexing

You do not need a manual step. Corpora listed in `RETRIEVAL_BOOTSTRAP_CORPORA`
(default `ahawr-workspace=/workspace`) are indexed on their first request. Every later request
runs an incremental sync first (`freshness_mode=sync`), so files changed by the Worker are
re-indexed chunk by chunk before the Reviewer's retrieval. Embeddings missing after an embedder
outage are back-filled on later syncs, with a 60 s back-off while the embedder is down.

For a large repository with a real embedding model, run the first index once to keep it out of
the task's timeout:

```powershell
docker compose exec ahawr-retrieval ahawr-retrieval index ahawr-workspace --root /workspace
```

External documentation can be pushed with `POST /index` as inline documents:

```json
{"corpus_id": "ahawr-docs",
 "documents": [{"path": "runbooks/deploy.md", "content": "# Deploy …", "version": "2026-09-20",
                "valid_until": "2027-01-01T00:00:00Z"}],
 "documents_mode": "upsert"}
```

## 3. Enable it in AHAWR v13

1. Import `AHAWR_v13.json`. It has the same workflow id as v12 and replaces it. The Run Manager
   is called by the id in `hermes_config.run_manager_workflow_id`, or `nhjwX1G7FiVTO2Ah` when the
   column is empty. The `Build … Run Input` Code nodes prepare its inputs, so no Execute Workflow
   node needs editing after an import.
2. Add these optional columns to the `hermes_config` Data Table (see `hermes_config.csv`):

   | column | example | meaning |
   |---|---|---|
   | `retrieval_enabled` | `true` | master switch (default: off) |
   | `retrieval_corpora_json` | `ahawr-workspace` | comma-separated corpus ids, or a JSON array of ids |
   | `retrieval_url` | `http://ahawr-retrieval:8500` | service URL inside the stack (default) |
   | `retrieval_timeout_ms` | `120000` | HTTP timeout; on timeout the task runs without context |
   | `retrieval_worker_max_tokens` | `6000` | Worker context budget |
   | `retrieval_reviewer_max_tokens` | `5000` | Reviewer context budget |
   | `retrieval_label` | `hybrid-v1` | configuration label written to retrieval logs for A/B analysis |

3. Optional, per mission: a `retrieval_corpora_json` column in `missions` overrides the corpora.
4. If you set `RETRIEVAL_API_KEY`, open `Retrieve Worker Context` and `Retrieve Reviewer Context`
   and set *Authentication → Generic → Header Auth* with `Authorization: Bearer <key>`, stored
   as an n8n credential (never in the Data Table).

### What changes in the workflow

```
All Tasks Completed? ─(run task)→ Worker Retrieval Enabled? ─yes→ Build Worker Retrieval Request → Retrieve Worker Context ─┐
                                                           └─no──────────────────────────────────────────────────────────→ Attach Worker Context → Worker Start
Reviewer State → Reviewer Retrieval Enabled? ─yes→ Build Reviewer Retrieval Request → Retrieve Reviewer Context ─┐
                                            └─no──────────────────────────────────────────────────────────────→ Attach Reviewer Context → Reviewer Start
```

* `Build * Retrieval Request` (Code) assembles the `/retrieve` request from the current task.
* `Retrieve * Context` (HTTP Request) calls `POST {retrieval_url}/retrieve` with `neverError` +
  `continueRegularOutput`. An unreachable container, an HTTP error or a timeout produces
  **no context**, and the task proceeds as in v12 (`retrieval_*_status = unavailable`, reason in
  `retrieval_*_error`).
* `Attach * Context` only adds `retrieval_*` fields to the in-flight item. All Data Table
  writes use explicitly mapped columns, so nothing retrieval-related is persisted to
  `Autonomous Agent Task State` / `Task Attempts`, and resume/recovery paths are unchanged.
* The Worker resume path (`Resume Worker?` → `Worker Start`) continues an existing Hermes session
  and intentionally skips retrieval.
* Worker input gets the context block after `SCOPE` and before `PREVIOUS REVIEW FEEDBACK`;
  Reviewer input gets it after `WORKER RESULT`.
* The request's `trace` carries only `mission_id`, `state_namespace`, `task_id`, `role` and
  `label` — no run, session, attempt or status identifiers.

## 4. rag-platform as external backend

No change to rag-platform is required.

1. In rag-platform (admin API/UI), create a project collection, e.g. `ahawr-code`, and a service
   API key allowed to write and search it. Pick a fixed owner UUID for AHAWR.
2. Set in `.env`: `RETRIEVAL_RAG_URL` (e.g. `http://host.docker.internal:8100`),
   `RETRIEVAL_RAG_API_KEY`, `RETRIEVAL_RAG_OWNER_ID`, `RETRIEVAL_RAG_PROJECT_ID`,
   `RETRIEVAL_RAG_COLLECTION`. Keep `RETRIEVAL_BACKEND=auto`.
3. After the next sync, every active chunk is mirrored into rag-platform as one document. Check
   `GET /health` → `backend.rag_platform.mirror.<corpus>.pending_chunks == 0`.

If rag-platform is unavailable, retrieval is served from the local index automatically, and
responses carry `degraded_reasons: ["rag_platform_unavailable:fallback_local"]`.

## 5. API summary

| Endpoint | Purpose |
|---|---|
| `POST /retrieve` | profile (`worker`/`reviewer`), corpora, `task`, `review`, optional `query`, `corpus_roots` (corpus id → root; corpora not yet indexed are indexed on first use, within `RETRIEVAL_ALLOWED_ROOTS`, host paths mapped via `RETRIEVAL_PATH_MAP`), `changed_paths` (paths the Worker changed; fragments are boosted, strongest for the reviewer profile; absent field = today's behaviour; also collected automatically for reviewer requests), `workspace_state` (`code_snapshot`, `docs_snapshot`, `doc_versions`, `strict`), `freshness_mode` (`trust`/`verify`/`sync`), `budget`, `options` (profile overrides), `cache` (`use`/`refresh`/`bypass`), `trace`, `include_candidates` |
| `POST /index` | `corpus_id`, `root`, `mode` (`incremental`/`full`), `paths`, `source_types`, `include_globs`, `exclude_globs`, `documents`, `documents_mode`, `force` |
| `POST /invalidate` | `corpus_id` + `paths` / `chunk_ids` / `all`, optional `source_type`, `reason`, `reindex` |
| `GET /corpora`, `GET /corpora/{id}` | snapshots, generations, chunk/file counts by status |
| `GET /health` | embedder, reranker, backend and mirror status |

The FastAPI schema is at `/docs` and `/openapi.json` inside the stack. The same operations are
available without HTTP through `ahawr-retrieval exec '<json or base64>'` inside the container,
which is useful for scripting and debugging.

Response notes: `degraded_reasons` lists faults (reranker/embedder/rag-platform outages,
fallbacks). `notes` are informational and not degradations — e.g. `RETRIEVAL_RERANKER=none`
(expected, not a fault) and `stale_corpus:<id>` (corpus root no longer exists; no fragments
served) are carried as notes, not as errors.

### 6. Usage metrics CLI

`ahawr-retrieval usage` joins the retrieval log with the claude-runner API and reports per
profile: selected files, opened files, precision, recall, token share, `ahawr-search` call
counts, and the most-missed files (opened but never selected).

```bash
ahawr-retrieval usage --runner-url http://localhost:8899 --since -7d
ahawr-retrieval usage --runner-url http://localhost:8899 --since -24h --profile reviewer --top 5
ahawr-retrieval usage --runner-url http://localhost:8899 --json
```

From inside the compose stack (container-to-container URL):

```bash
docker compose exec ahawr-retrieval ahawr-retrieval usage --runner-url http://claude-runner:8700 --since -24h
```

`--since` takes a unix timestamp, an ISO-8601 value, or a relative offset (`-24h`, `-7d`,
`-1w`); omit it for all logged requests.

`ahawr-search` (in the claude-runner image) is the Worker-facing CLI for on-demand retrieval:

```bash
ahawr-search "where is the retry delay applied?" --k 8 --budget 1500
```

The default budget is 1500 tokens. The working directory `/tmp/<X>` is resolved with `realpath`
(a symlink into `/d/rag-tmp/<X>`); only `/d/` and `/workspace` roots are accepted; if the folder is not available to the service the CLI prints
`folder <resolved> is unavailable to the retrieval service` and exits 0.

A `/retrieve` request with `changed_paths` (reviewer profile):

```json
{
  "profile": "reviewer",
  "corpora": ["ahawr-workspace"],
  "corpus_roots": {"ahawr-workspace": "/workspace"},
  "task": {"title": "…", "objective": "…"},
  "changed_paths": ["src/runner/api.py", "tests/test_api.py"],
  "budget": {"max_tokens": 5000},
  "freshness_mode": "sync"
}
```

### 7. Live-task A/B (RAG on vs. RAG off)

Procedure for one live task:

1. **Run A (RAG on)** — enable retrieval for the task (`retrieval_enabled=true`,
   `retrieval_label=hybrid-v1`). Record: file reads (from the runner event stream), wall-clock
   time, and the Reviewer's review score/decision.
2. **Run B (RAG off)** — disable retrieval (`retrieval_enabled=false`, `retrieval_label=off`)
   for the same task. Record the same metrics.
3. **Compare** — retrieval calls, context tokens, first-pass rate, retries, total task
   latency, and review score.
4. **Order of applying** — keep the order fixed across tasks: RAG on first, RAG off second
   (or the reverse, consistently), so environment drift is not attributed to the label.

For an offline frozen-snapshot comparison instead, run `ahawr-retrieval-eval run` per
configuration label and compare against the baseline; see
`retrieval-service/eval/results/2026-09-30-rag-usage-after.md` for a worked same-corpus
example (4 runs: baseline vs. modified code, gold v1 and gold-ru, identical snapshot).
