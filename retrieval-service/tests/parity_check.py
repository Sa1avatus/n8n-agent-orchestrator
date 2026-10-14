"""Manual check, not a pytest test: do SQLite and PostgreSQL return the same top results for the same corpus and queries?

    set RETRIEVAL_TEST_DATABASE_URL=postgresql://...      (the same DSN the tests use)
    PYTHONPATH=src python tests/parity_check.py <folder> "query one" "query two" ...

Both services index the folder with the built-in lexical + hash embedder settings of the tests (NoopReranker); prints the top-8 paths of each
backend per query and the overlap.
"""
import os
import sys
import tempfile
import uuid
from pathlib import Path

from ahawr_retrieval.config import Settings
from ahawr_retrieval.models import IndexRequest, RetrieveRequest
from ahawr_retrieval.reranker import NoopReranker
from ahawr_retrieval.service import RetrievalService

folder = Path(sys.argv[1]).resolve()
queries = sys.argv[2:] or ["how does the sync work", "where is the cache stored", "lexical search ranking"]
dsn = os.environ["RETRIEVAL_TEST_DATABASE_URL"]
schema = "parity_" + uuid.uuid4().hex[:8]
tmp = Path(tempfile.mkdtemp())
common = {"allowed_roots": [str(folder), str(tmp)], "sync_min_interval_seconds": 0.0}
sqlite = RetrievalService(Settings(data_dir=tmp / "sq", **common), reranker=NoopReranker())
pg = RetrievalService(Settings(data_dir=tmp / "pg", database_url=dsn, database_schema=schema, **common), reranker=NoopReranker())
for name, svc in (("sqlite", sqlite), ("postgres", pg)):
    result = svc.index(IndexRequest(corpus_id="c", root=str(folder)))
    print(name, "indexed:", result.stats if hasattr(result, "stats") else result)
total = same = 0
for q in queries:
    tops = {}
    for name, svc in (("sqlite", sqlite), ("postgres", pg)):
        resp = svc.retrieve(RetrieveRequest(profile="worker", corpora=["c"], task={"title": q, "objective": q}, cache="bypass"))
        tops[name] = [f"{c.path}:{c.start_line}" for c in resp.chunks[:8]]
    a, b = tops["sqlite"], tops["postgres"]
    overlap = len(set(a) & set(b))
    total += max(len(a), len(b))
    same += overlap
    print(f"\n== {q}\n  overlap {overlap}/{max(len(a), len(b))}")
    print("  sqlite  :", a[:4])
    print("  postgres:", b[:4])
print(f"\nTOTAL overlap {same}/{total} = {100 * same / max(total, 1):.0f}%")
pg.close()
sqlite.close()
import psycopg  # noqa: E402

with psycopg.connect(dsn, autocommit=True) as conn:
    conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
