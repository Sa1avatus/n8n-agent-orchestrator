# Search latency — call path of one `ahawr-search`

This note traces a single `ahawr-search` invocation from the Worker's CLI to the
retrieval service and back. It records **where** each step happens (`file:line`)
and, for each step, whether that step **walks the tree**, **hashes files**,
**re-embeds**, or **queries the DB** on *every* call. No timing numbers are
estimated here; every claim points at a code line.

The trace follows the Worker profile (`search_cli.py:82` sends `profile: "worker"`).
The Worker profile's `freshness_mode` defaults to `"sync"` (`profiles.py:110`),
and `sync_wait_seconds` defaults to `-1.0` (`config.py:158`), so the inline sync
branch runs on every request (`service.py:330`).

## Call path

### 1. Parse arguments — `search_cli.py:main` (`search_cli.py:185`)
Builds the query/root/budget/url/timeout. `args.query = (args.query_option or " ".join(words)).strip()`
(`search_cli.py:251`), empty-query check at `search_cli.py:252-253`, root falls back to `"."`
(`search_cli.py:258`). `AHAWR_RETRIEVAL_URL` is read as the `--url` default
(`search_cli.py:236`); constants live at `search_cli.py:21-32`.

- Walks the tree? No.
- Hashes files? No.
- Re-embeds? No.
- Queries the DB? No.

### 2. Resolve root, skip gate, corpus slug — `search_cli.py:search_text` (`search_cli.py:148`)
`resolve_root(root)` (`search_cli.py:159` → `resolve_root` at `search_cli.py:35`) applies
`os.path.realpath` (`search_cli.py:40`) and checks the result against
`ALLOWED_PREFIXES` (`search_cli.py:41-42`). If `AHAWR_SEARCH_SKIP_PATH` is truthy
(`search_cli.py:145`) and `os.path.exists(query)` (`search_cli.py:162`), retrieval is skipped
with a one-line message instead of a network call. The directory is slugged into a corpus id
by `corpus_slug(resolved)` (`search_cli.py:165` → `corpus_slug` at `search_cli.py:47`,
regex at `search_cli.py:54`), and the `{slug: resolved}` pair becomes `corpus_roots`.

- Walks the tree? No (`os.path.realpath` + `os.path.exists` only).
- Hashes files? No.
- Re-embeds? No.
- Queries the DB? No.

### 3. POST `/retrieve` — `search_cli.py:fetch_detailed` (`search_cli.py:62`)
POSTs to `url + "/retrieve"` (`search_cli.py:78-79`) with the JSON body
`{query, profile: "worker", budget: {max_tokens}, corpora: [slug],
corpus_roots: {slug: resolved}}` (`search_cli.py:80-86`); budget is clamped by
`clamp_budget` (`search_cli.py:139`, call at `search_cli.py:171`). On a timeout the
reason notes that "a folder searched for the first time is indexed on this call"
(`search_cli.py:100-104`).

- Walks the tree? No (worker side).
- Hashes files? No (worker side).
- Re-embeds? No (worker side).
- Queries the DB? No (worker side). Network boundary.

### 4. Route dispatch — `api.py:retrieve` (`api.py:69-71`)
`POST /retrieve` (auth-gated by `authorize`, `api.py:50-55`, using
`AHAWR_RETRIEVAL_API_KEY`) → `service.retrieve(body)` (`api.py:71`).

- Walks the tree? No.
- Hashes files? No.
- Re-embeds? No.
- Queries the DB? No (auth check only).

### 5. The retrieve handler — `service.py:RetrievalService.retrieve` (`service.py:301`)
This is the service-side orchestrator. Sub-steps:

#### 5a. Profile + freshness mode
`resolve_profile(request)` (`service.py:305`); `freshness_mode =
request.freshness_mode or profile.freshness_mode` (`service.py:306`). Worker
default `"sync"` (`profiles.py:110`).

- DB? No.
- Walk/hash/re-embed? No.

#### 5b. `_corpora` — resolve corpora, first-use indexing (`service.py:311` → `651-671`)
`store.get_corpus(corpus_id)` is a DB query (`store.py:317`; for PostgreSQL this
runs through `PgStore._query` at `pg_store.py:298`). If the corpus is unknown
but a root is provided, it is indexed now on this call:
`self.index(IndexRequest(...))` (`service.py:663`) — the **cold** full index.

- Walks the tree? Only on first use of a corpus (`service.py:659-664`) →
  `indexer.py:297` → `_sync_workspace` → `_walk` (`indexer.py:493`).
- Hashes files? Only on first use (`service.py:663` → `indexer.py:634`).
- Re-embeds? Only on first use (`service.py:663` → `indexer.py:968`).
- Queries the DB? Yes, on every call (`store.py:317` via `pg_store.py:298`);
  first-use also writes (`store.py:741`, `indexer.py:926`).

#### 5c. Freshness sync — `service.py:312-344` (inline for the Worker default)
`sync_wait_seconds` default `-1.0` (`config.py:158`) makes the inline branch run
(`service.py:330`): `self.indexer.sync(corpus_id)` (`service.py:335`). `sync`
(`indexer.py:361`) is rate-limited: if `paths is None` and the corpus was synced
within `sync_min_interval_seconds` (default `2.0`, `config.py:157`), it returns
`None` without walking (`indexer.py:366-371`); otherwise it calls
`self.index(...)` → `_sync_workspace` → `_walk` (`indexer.py:372`).

- Walks the tree? Only when the `sync_min_interval_seconds` window has elapsed
  (`indexer.py:366-371`); otherwise skipped.
- Hashes files? Only for files whose mtime/size changed during a sync
  (`indexer.py:616-617`); unchanged files are "touched" without re-reading
  (`indexer.py:739`).
- Re-embeds? No on a clean sync; only new/changed content vectors are missing
  (`indexer.py:968`).
- Queries the DB? Yes — `get_corpus`, `changes_since`, `get_chunks`,
  `get_embeddings`, writes (`indexer.py:739` writes the manifest).

#### 5d. Query build — `service.py:348-349`
`build_query(request)` (`service.py:348`); `match_query = fts_match_query(query)`
(`service.py:349` → `fts_match_query` at `service.py:132`). Pure in-memory shaping.

- DB? No. Walk/hash/re-embed? No.

#### 5e. Cache lookup — `service.py:379-388`
`self.cache.lookup(...)` (`service.py:379`) → `store.cache_get` (a DB query on
`retrieval_cache`, `store.py:731`). On a cache hit the response is rebuilt and
returned at `service.py:404-405`, **skipping** candidate retrieval, ranking and
reranking. The cache key is scoped by profile, config_id, corpora and task
(`cache.py:42-51`).

- Walks the tree? No.
- Hashes files? No (reads stored content hashes only).
- Re-embeds? No.
- Queries the DB? Yes, on every call when caching is used (`store.py:731`).

#### 5f. `_candidate_lists` — `service.py:434` → `673-753`
Builds the lexical / vector / symbol candidate lists. For the local Worker
backend (`service.py:687`):

- Lexical: `self.store.lexical_search(...)` (`service.py:740` → `store.py:666`;
  PostgreSQL override is `PgStore.lexical_search` at `pg_store.py:378`, which runs
  `ts_rank_cd` with a 600-second document-frequency cache, `pg_store.py:358` and
  `pg_store.py:188`). **DB query on every call.**
- Vector: `self.vector_index.search(...)` (`service.py:747` →
  `vector_index.py:96`) → `_refresh` (`vector_index.py:49`), which reads
  `store.changes_since` (`store.py:568`) on every search to catch new/changed
  chunks, and only reads `store.get_chunks`/`store.get_embeddings`
  (`store.py:450`/`store.py:615`) when there are changes (`vector_index.py:57-68`).
  The actual similarity is in-memory numpy (`vector_index.py:110`).
  **DB query on every call** (`changes_since`, `store.py:568`); full vector load
  only on first `_load` (`vector_index.py:37-39`).
- Symbol (worker enables `retrievers.symbol`): `self._symbol_search(...)`
  (`service.py:446` → `755-802`) → `store.symbol_lookup` (`store.py:697`),
  `store.path_lookup` (`store.py:709`), `store.active_chunks_for_paths`
  (`store.py:463`), `store.get_chunks` (`store.py:450`). **DB queries on every
  call.**

- Walks the tree? No.
- Hashes files? No.
- Re-embeds? No.
- Queries the DB? Yes — lexical search, vector index refresh, and symbol/path
  lookups all hit the store on every call (`store.py:666`, `store.py:568`,
  `store.py:697`, `store.py:709`, `store.py:463`, `store.py:450`; PostgreSQL
  dispatch via `pg_store.py:298`).

#### 5g. `_hydrate` — `service.py:453` → `874-893`
`store.get_chunks` (`store.py:450`), `store.get_files` (`store.py:416`),
`store.file_line_count` (`store.py:476`) per candidate. **DB queries on every
call.**

- Walks the tree? No. Hashes files? No. Re-embeds? No. **DB? Yes.**

#### 5h. `apply_hard_filters` — read-time freshness verification — `service.py:457` → `filters.py:21`
For `freshness_mode in {"verify", "sync"}` (`filters.py:94`) and workspace
origin files, `verifier.current_hash(corpus.root, record.path)`
(`filters.py:100`) is called for each candidate. This is the
`WorkspaceVerifier` (`indexer.py:235`), which reads the file and computes
`sha256_hex(Path(absolute).read_bytes())` (`indexer.py:253`), **memoized by
mtime+size** (`indexer.py:250`): on every retrieve it verifies candidate files
that are not already cached by mtime.

- Walks the tree? No (individual file reads only).
- Hashes files? Yes, on every call for candidate files not cached by mtime
  (`indexer.py:253`).
- Re-embeds? No.
- Queries the DB? No (in-process `WorkspaceVerifier` cache; `filters.py:94-105`).

#### 5i. Rerank — `service.py:485`
`self.reranker.rerank(query.rerank_text, ...)` (`service.py:485`). For the local
Worker this is typically the CPU cross-encoder or a `NoopReranker`
(`service.py:110`); when an HTTP reranker is configured it is a network call to
`RETRIEVAL_RERANKER_URL` (`config.py:141`).

- DB? No (network/CPU only). Walk/hash/re-embed? No.

#### 5j. Ranking + context selection — `service.py:521-533`
`rank_candidates` (`service.py:521` → `ranking.py`), `select_context`
(`service.py:526` → `context.py`). In-memory.

- DB/walk/hash/re-embed? No.

#### 5k. Cache store on miss — `service.py:549-561`
`self.cache.put(...)` (`service.py:550` → `store.cache_put`, `store.py:741`) — a
DB write only when the request was a cache miss and selected chunks were
produced.

- DB? Yes, only on a miss. Walk/hash/re-embed? No.

#### 5l. Feature log — `service.py:591`
`self._log(...)` (`service.py:591`) writes the request row + candidate rows to
the retrieval log DB (`RetrievalLog` at `service.py:163`). **DB write on every
call.**

- DB? Yes. Walk/hash/re-embed? No.

### 6. Sync / index refresh — `indexer.py:sync` / `index` / `_sync_workspace` / `_walk`
(Reached from 5b first-use `service.py:663` and 5c `service.py:335`.)

- `sync` (`indexer.py:361`): returns `None` (no work) within
  `sync_min_interval_seconds` of the last sync (`indexer.py:366-371`); otherwise
  `self.index(IndexRequest(...))` (`indexer.py:372`).
- `index` (`indexer.py:297` → `_sync_workspace` `indexer.py:565`).
- `_walk` (`indexer.py:493`): the tree walk. Prefers `git_files(root)`
  (`indexer.py:497` → `git_files` at `indexer.py:170`, a `git ls-files` subprocess)
  when `(root / ".git").exists()` (`indexer.py:173`); otherwise falls back to
  `os.walk(root)` (`indexer.py:510`).
- Per-file hashing: `sha256_hex(raw)` for the whole file (`indexer.py:634`);
  unchanged files are detected by mtime+size alone (`indexer.py:616-618`). Per-chunk
  hashing: `sha256_hex(draft.content)` (`indexer.py:772`).
- Chunking: `chunk_file(path, text, self.chunking)` (`indexer.py:749` →
  `chunking.py:243`; `classify_path` at `chunking.py:222`).
- Re-embed: `_embed` (`indexer.py:968`, called at `indexer.py:926`) calls
  `embedder.embed_documents` **only** for content hashes with no stored vector
  (`indexer.py:999`); existing vectors are counted as `reused`
  (`indexer.py:993`) after a `get_embeddings` lookup (`indexer.py:991`).

- Walks the tree? Yes, on first-use and on each sync that is outside the
  `sync_min_interval_seconds` window (`indexer.py:493`, gated at `indexer.py:369`).
- Hashes files? Yes — every file on first index (`indexer.py:634`); on a sync only
  mtime/size is compared and unchanged files are skipped (`indexer.py:616-617`).
- Re-embeds? Yes, but only for hashes missing a vector (`indexer.py:999`);
  vectors are reused from the `embeddings` table (`store.py:615`,
  `indexer.py:991-993`).
- Queries the DB? Yes (`chunks_for_path`, `insert_chunk`, `put_embeddings`,
  `mark_stale`, etc.).

### 7. Per-call store queries — `store.py` / `pg_store.py`
The store used is `PgStore` when `RETRIEVAL_DATABASE_URL` is set
(`service.py:157-160`); otherwise the SQLite `Store` (`service.py:162`). `PgStore`
subclasses `Store` and overrides `_query` (`pg_store.py:298`) and `lexical_search`
(`pg_store.py:378`); everything else is inherited from `Store` and reaches the
database on every call:

| Query (per retrieve) | Defined at | Runs on every call |
|---|---|---|
| `get_corpus` | `store.py:317` | yes (5b) |
| `lexical_search` (SQLite FTS5 `bm25`; PG `ts_rank_cd` + 600 s DF cache `pg_store.py:358`/`pg_store.py:188`) | `store.py:666`, `pg_store.py:378` | yes (5f) |
| `changes_since` (vector index refresh) | `store.py:568` | yes (5f) |
| `get_chunks` / `get_files` / `file_line_count` | `store.py:450`, `store.py:416`, `store.py:476` | yes (5g) |
| `symbol_lookup` / `path_lookup` / `active_chunks_for_paths` | `store.py:697`, `store.py:709`, `store.py:463` | yes (5f, symbol) |
| `get_embeddings` (only when a content hash lacks a local vector) | `store.py:615` | only on cache miss / new content |
| `cache_get` / `cache_put` / `cache_touch` | `store.py:731`, `store.py:741`, `store.py:766` | yes on lookup; put only on miss |
| `files_indexed_since` (Reviewer automatic boost) | `store.py:594` | worker: no (5b/5c are the only sync reads for the worker) |

## Walk ignore rules

The walk is `Indexer._walk` (`indexer.py:493`). It is called from
`_sync_workspace` (`indexer.py:565`) and the first-use `index` (`service.py:663`).

The directories that are **always skipped** (`EXCLUDED_DIRS`, `indexer.py:51-82`,
checked at `indexer.py:157` and `indexer.py:516`):

```
.git, .hg, .svn, node_modules, .venv, venv, env, __pycache__, .mypy_cache,
.pytest_cache, .ruff_cache, .tox, dist, build, target, coverage, .idea, .vscode,
.next, .nuxt, .terraform, site-packages, .cache, secrets, credentials, tokens,
.eggs, htmlcov
```

Check of the four named folders against `EXCLUDED_DIRS`:

- `.git` — skipped (`indexer.py:53`).
- `.venv` — skipped (`indexer.py:57`).
- `node_modules` — skipped (`indexer.py:56`).
- `.tools` — **not** in `EXCLUDED_DIRS`; it is walked unless `.gitignore` excludes
  it (no built-in skip). As a dotfile *file* it would classify as plain text
  (`classify_path` returns `("code", "text")` at `chunking.py:240`).

Files are skipped by name/glob in `EXCLUDED_FILES` (`indexer.py:84-114`):

```
.env, .env.*, *.pem, *.key, *.p12, *.pfx, id_rsa*, id_dsa*, id_ecdsa*, id_ed25519*,
*.keystore, *.jks, credentials*.json, secrets.*, *.secret, *.sqlite, *.sqlite3, *.db,
*.log, package-lock.json, pnpm-lock.yaml, yarn.lock, poetry.lock, uv.lock, Cargo.lock,
go.sum, *.min.js, *.min.css, *.map
```

`.env` / `secrets` are never indexed, matching the worker rule that forbids reading
`.env` and secrets. `ALLOWED_DOTFILES` (`indexer.py:115`) contains only `.env.example`.
`DEFAULT_EXCLUDE_GLOB` artefacts (`*.bak`, `*.orig`, `*~`, …) are at
`indexer.py:119-128`.

`.gitignore` handling: in a git work tree the walk uses `git ls-files --cached
--others --exclude-standard` (`git_files`, `indexer.py:170-197`), so git applies
all `.gitignore` levels itself; `EXCLUDED_DIRS` and `EXCLUDED_FILES` are then
re-applied to the git result (`indexer.py:502-506`). In a non-git tree the
fallback `os.walk` (`indexer.py:510`) applies the root `.gitignore` approximately
via `_gitignore` (`indexer.py:534-540`) and `_ignored` (`indexer.py:543-563`,
no negation support). Symlinks are never followed (`indexer.py:506`,
`indexer.py:517`, `indexer.py:522`), and binary files are skipped by
`classify_path` returning `None` (`chunking.py:236`, the `BINARY_SUFFIXES` set at
`chunking.py:93`).

## Cold call vs warm call

A **cold call** is the first retrieve of a corpus (the corpus row is unknown to the
store). A **warm call** reuses an already-indexed corpus. The Worker profile's
inline sync (`service.py:330-344`) means the difference is the sync that runs.

| What differs | Cold call | Warm call |
|---|---|---|
| Walk the workspace tree | `sync`→`index`→`_sync_workspace`→`_walk` walks every text file (`service.py:663` → `indexer.py:372` → `indexer.py:565` → `indexer.py:493`) | `sync` returns early without walking within `sync_min_interval_seconds` (`indexer.py:366-371`); no walk at all |
| Hash every file | Each file is read and hashed once at `sha256_hex(raw)` (`indexer.py:634`) | Skipped — unchanged files detected by mtime+size (`indexer.py:616-617`), then only "touched" (`indexer.py:739`); candidate freshness is re-verified on demand by `WorkspaceVerifier.current_hash` (`indexer.py:253`, memoized at `indexer.py:250`) |
| Re-embed | `_embed` computes vectors for every missing content hash via `embed_documents` (`indexer.py:999`) | Not reached; vectors reused from the `embeddings` table (`indexer.py:991-993`) |
| Load the vector matrix | `_refresh`→`_load` reads all active vectors via `active_vectors` (`vector_index.py:37-39` → `store.py:652`) | `_refresh` only reads `changes_since` (`vector_index.py:57` → `store.py:568`) and reuses the in-memory matrix |
| Result | New chunks and embeddings written; `code_generation`/`docs_generation` bumped (`indexer.py:929-931`) | Reads against the existing index; writes only the retrieval/log/cache rows |
| Cache | Miss on first run of a query → stored at `store.py:741` (`service.py:550`) | May hit (`store.py:731` lookup at `service.py:379`) and short-circuit candidate ranking (`service.py:404-405`) |

## Baseline timings

Measured by a Worker using `ahawr-search` (CLI entry `main`, `search_cli.py:185`).
The query was the same cheap string `"python"` for every call, `--k 1 --budget 100`.
No flag was set for `AHAWR_SEARCH_SKIP_PATH`, so the skip gate did not trigger.

**Exact timing command** (one call per folder):

```bash
start=$(date +%s.%N); ahawr-search "python" --k 1 --budget 100 --root <folder> --timeout <timeout>; end=$(date +%s.%N); awk "BEGIN {printf \"%.3f\", $end - $start}"
```

`<folder>` was the absolute path under `--root`; `<timeout>` was `90` for the cold
folder (model-bench, allow time for first-use indexing) and `30` for /d/n8n.

File counts (`find -type f`; excluded dirs: `.git`, `.venv`, `node_modules`, `.tools`):

| Folder | Total files | Excluding `.git`/`.venv`/`node_modules`/`.tools` | Searched before | Series start (UTC) | Call 1 (s) | Call 2 (s) | Call 3 (s) |
|---|---|---|---|---|---|---|---|
| `/d/ClaudeProjects/model-bench` | 181,255 | 16,200 | no | 2026-10-30T04:01:14Z | 4.314 | 1.837 | 1.695 |
| `/d/n8n` | 29,428 | 29,312 | yes (connectivity check) | 2026-10-30T04:01:44Z | 0.229 | 0.219 | 0.230 |
| `/d/ClaudeProjects/strata-eval` | 5,235 | 5,205 | no | 2026-10-30T04:06:30Z | 0.476 | 0.460 | 0.456 |

Notes:

- model-bench call 1 is a **cold call** — first retrieve of the corpus, so the
  service walks and indexes on this call (`service.py:663` → `indexer.py:372`).
  Calls 2 and 3 are warm (index reused, `indexer.py:366-371`).
- /d/n8n was warmed by a connectivity check before the measurement series, so
  all three calls are warm.
- Each call stayed well under the 100-second limit (longest: 4.314 s).
- strata-eval was checked with one `ahawr-search` call before timing: it took
  5.593 s (cold, first-use indexing), which warmed the corpus. The three timed
  calls are therefore all warm.

## Cause

### Suspected step

On a warm call the inline sync branch runs `self.indexer.sync(corpus_id)` on every
request (`service.py:330-335`). `sync` (`indexer.py:361`) walks the whole workspace
only when the corpus was **not** synced within `sync_min_interval_seconds`
(default `2.0`, `config.py:80`; early-return at `indexer.py:366-371`); otherwise it
returns `None` without touching the tree. That gate is exactly why the baseline
warm calls are fast — `/d/n8n` ~0.22 s and strata-eval ~0.46 s, because those series
ran inside the 2 s window and `sync` returned without walking (the ~0.2/0.5 s is DB
I/O only). Once a warm call is older than 2 s, `sync` → `self.index(...)` →
`_sync_workspace` (`indexer.py:565`) → `_walk` (`indexer.py:493`) plus the per-file
stat/hash loop (`indexer.py:591-634`). The slow step is therefore **`Indexer._walk`
(`indexer.py:493`)**, reached from `sync` (`indexer.py:361`, called at
`service.py:335`) on every warm call that is older than 2 s.

### What was timed (local only)

Throwaway script `/tmp/t002_cause.py` (not in the repo), `retrieval-service/src`
on `sys.path`, run by `retrieval-service/.test-venv`. `time.perf_counter`, 3 runs,
minimum shown. The live Postgres and the embedder were **not** touched: the manifest
fetch (`store.files`, `indexer.py:577`), the manifest writes (`upsert_file`/`_apply_file`,
`indexer.py:739`) and `_embed` (`indexer.py:968`) could **not** be isolated — they are
DB/service calls, so only the local disk parts (walk, stat, hash, freshness check)
were measured.

`_walk` (`indexer.py:493`) branches on `git_files(root)` (`indexer.py:170`):

| Step (min of 3 runs) | `/d/n8n` (git tree) | `/d/ClaudeProjects/strata-eval` (no `.git`) |
|---|---|---|
| Walk `git_files` subprocess (`indexer.py:170`) | 1.530 s | n/a |
| Walk `Indexer._walk` (`indexer.py:493`) | 2.277 s | 22.415 s |
| `stat` every listed file (`indexer.py:600`) | 0.738 s (631 f.) | 16.266 s (5,007 f.) |
| `sha256_hex` read+hash every file (`indexer.py:634`, `text.py:89`) | 1.469 s (631 f.) | 24.230 s (5,007 f.) |
| Freshness `current_hash` cold (`indexer.py:242`) | 2.263 s (631 f.) | 37.453 s (5,007 f.) |
| Freshness `current_hash` warm, 2nd pass (`indexer.py:250`) | 0.746 s | 15.806 s |

`git_files` lists 631 files for `/d/n8n` (`git ls-files --exclude-standard` honours
`.gitignore`; `find` sees 29,312 on disk). `_walk` listed 5,007 for strata-eval via
the `os.walk` fallback (`indexer.py:510`, because there is no `.git`).

### cProfile — top by cumulative time (walk + stat + hash combined)

`/d/ClaudeProjects/strata-eval`, total **60.459 s**:

```
   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
        1    0.041    0.041   22.415   22.415 indexer.py:493(_walk)
        1    0.049    0.049   22.328   22.328 do_hash
     5007    0.026    0.000   22.136    0.004 pathlib.py:1015(read_bytes)
     6379    0.014    0.000   19.339    0.003 genericpath.py:51(islink)
     6379   19.322    0.003   19.322    0.003 {built-in method posix.lstat}
        1    0.031    0.031   15.716   15.716 do_stat
     5008   15.497    0.003   15.618    0.003 {built-in method posix.stat}
      600    0.012    0.000    4.018    0.007 os.py:287(walk)
     5007    3.634    0.001    3.634    0.001 {method 'read' of '_io.BufferedReader'}
     1198    0.002    0.000    1.716    0.001 {built-in method builtins.sorted}
     1197    0.004    0.000    1.714    0.001 indexer.py:513(<genexpr>)
```

`/d/n8n`, total **4.781 s**:

```
   ncalls  tottime  percall  cumtime  percall filename:lineno(function)
        1    0.007    0.007    2.452    2.452 indexer.py:493(_walk)
        1    0.000    0.000    1.599    1.599 indexer.py:170(git_files)
       35    1.595    0.046    1.595    0.046 {method 'poll' of 'select.poll' objects}
        1    0.005    0.005    1.567    1.567 do_hash
   631    0.003    0.000    1.546    0.002 pathlib.py:1015(read_bytes)
   633    0.776    0.001    0.791    0.001 {built-in method _io.open}
        1    0.003    0.003    0.762    0.762 do_stat
   639    0.001    0.000    0.762    0.001 genericpath.py:51(islink)
   639    0.751    0.001    0.760    0.001 {built-in method posix.lstat}
```

`_walk` is the top cumulative entry in both profiles. On `/d/n8n` the walk is the
`git ls-files` subprocess (`indexer.py:170`); on the non-git tree the walk is
`os.walk` (`indexer.py:510`) plus one `os.path.islink`/`lstat` per directory entry
(`indexer.py:517`/`522`) — `lstat` alone is 19.3 s here (6,379 calls ≈ 3 ms each),
versus the ~3 µs a `stat` costs on a fast disk.

### Cost of the ignored folders (.git/.venv/.tools/node_modules)

A bare `os.walk` (no `islink`) that descends into those dirs vs. one that prunes
them by name (same logic, name-prune only):

| Folder | Files inside those 4 dirs | `os.walk` full | `os.walk` skip | Ignored cost |
|---|---|---|---|---|
| `/d/n8n` | 122 (`.git` only) | 17.16 s | 16.96 s | ~0.20 s |
| strata-eval | 0 | 4.51 s | 4.31 s | ~0 s (noise; 0 ignored files) |

Cross-check — traversing *only* the ignored dirs: ~0.23 s on `/d/n8n` (122
files) and ~0.005 s on strata-eval (no such dirs present). For both folders the
ignored directories are tiny or absent, so pruning them is cheap. **The ignored
folders are not the cause.** The bulk of an `os.walk` is traversing the non-ignored
files on this disk: `/d/n8n` `os.walk` full = 15.2 s even with no `islink` and no
`.git` entered, vs `git_files` (which honours `.gitignore`) at 1.53 s. (An earlier
"skip-with-`islink`" run read 21 s / 5.8 s; discarded because the per-entry `islink`
it added is itself an `lstat` cost, not an "ignored-folder" cost.)

### Verdict

The cause **is in retrieval-service code that may be changed**: `Indexer._walk`
(`indexer.py:493`), re-run on every warm call older than 2 s (`sync` at
`indexer.py:361`, called from `service.py:335`). It re-walks the entire workspace
on each such call instead of diffing, and its `os.walk` fallback issues one
`os.path.islink` (`lstat`) per directory entry (`indexer.py:517`/`522`). The walk's
magnitude is amplified by disk latency (~3 ms per `lstat`/`stat` on this WSL2 →
Windows `D:` mount), which is outside the code's control: `git_files`
(`indexer.py:170`) avoids it by letting `git ls-files` honour `.gitignore`
(1.53 s vs 15.2 s bare `os.walk`). The reported 7–10 s warm call is the `sync`
walk (`indexer.py:493`) firing on a warm call that passed the 2 s window; the
0.22 s / 0.46 s baseline warm calls are explained by that 2 s early-return
(no walk). DB and embedder work in the sync path could not be isolated for this
measurement.
