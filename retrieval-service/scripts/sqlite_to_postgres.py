"""Copy what is expensive to recompute from the SQLite index into the PostgreSQL store.

    python sqlite_to_postgres.py --sqlite /data/index.sqlite --dsn postgresql://... [--schema retrieval] [--corpora ahawr-workspace,x]

Copied: the embeddings (the CPU cost of an index: 122 000 vectors took hours) of the chosen corpora's active chunks, and the corpus rows (root and
config, generations start again at 0). NOT copied: files, chunks, the full-text index, symbols, caches: the first sync of a corpus in the
PostgreSQL store rebuilds them in minutes and reuses the copied embeddings by content hash.
Safe to repeat (existing rows are kept). The SQLite file is opened read-only.
"""
import argparse
import json
import sqlite3
import sys
import time

from ahawr_retrieval.pg_store import PgStore

parser = argparse.ArgumentParser()
parser.add_argument("--sqlite", required=True)
parser.add_argument("--dsn", required=True)
parser.add_argument("--schema", default="retrieval")
parser.add_argument("--corpora", default="", help="comma separated corpus ids; empty = all corpora")
parser.add_argument("--batch", type=int, default=5000)
args = parser.parse_args()

src = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True, timeout=60)
src.row_factory = sqlite3.Row
store = PgStore(args.dsn, args.schema)
wanted = [c.strip() for c in args.corpora.split(",") if c.strip()]
marks = ",".join("?" * len(wanted))

corpus_rows = src.execute(
    "SELECT corpus_id, root, config_json FROM corpora" + (f" WHERE corpus_id IN ({marks})" if wanted else ""), wanted
).fetchall()
for row in corpus_rows:
    store.ensure_corpus(row["corpus_id"], row["root"], json.loads(row["config_json"] or "{}"))
print(f"corpora copied: {len(corpus_rows)}")

where = f"WHERE e.content_hash IN (SELECT content_hash FROM chunks WHERE status = 'active' AND corpus_id IN ({marks}))" if wanted else ""
total = src.execute(f"SELECT COUNT(*) FROM embeddings e {where}", wanted).fetchone()[0]
print(f"embeddings to copy: {total}")
cursor = src.execute(f"SELECT e.model_id, e.content_hash, e.dim, e.vector, e.created_at FROM embeddings e {where}", wanted)
done = 0
started = time.time()
while True:
    rows = cursor.fetchmany(args.batch)
    if not rows:
        break
    with store.transaction() as conn:
        conn.executemany(
            "INSERT INTO embeddings(model_id, content_hash, dim, vector, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (model_id, content_hash) DO NOTHING",
            [(r["model_id"], r["content_hash"], r["dim"], bytes(r["vector"]), r["created_at"]) for r in rows],
        )
    done += len(rows)
    print(f"  {done}/{total} ({time.time() - started:.0f} s)", flush=True)
store.close()
print("done")
sys.exit(0)
