"""Selection share of retrieval log, read-only.

Reads `retrieval_logs.sqlite` in read-only mode (no container restart, no writes).
By default it isolates the GDN mission corpora (`d-openaiprojects-llama-cpp`,
`dcfr-c060ca97`) so only their requests/candidates are counted. Use `--corpora`
to target a different set (e.g. `ahawr-repo` for the gold-eval logs).

By default, a request matches if its `corpora_json` contains ANY of the
`--corpora` values (order-insensitive), and candidates are restricted to those
corpora. Pass `--exact-set` to keep the old behaviour: the request's
`corpora_json` must be exactly the requested set (also order-insensitive).

Optionally filter requests by mission: `--mission` keeps only requests whose
`trace_json` mission field (`json_extract(r.trace_json, '$.mission_id')`, the
same key `cache.py`/`service.py` use for the scope key) equals the given value.
`--since` / `--until` bound `r.ts`.

Each selected row's line count is resolved against the root of *its own* corpus.
Pass corpus roots explicitly with `--root-map corpus_id=path ...` (repeatable), or
use the shorthand `--root` as a fallback root for every corpus. A `.patch`/`.diff`
path whose file cannot be resolved at its corpus root is reported as `unknown`
(instead of being silently counted as 0 lines); it is excluded from the
">1000 lines" bucket and listed in a separate note.

Reported, for the selected fragments:
  - number of matched requests
  - total selected fragments and total tokens
  - (a) share taken by backup/artifact files
  - (b) share taken by .patch/.diff files longer than 1000 lines
  - top-10 selected paths
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

try:
    from ahawr_retrieval.chunking import LARGE_PATCH_LINES
except ImportError:  # run without the package installed
    LARGE_PATCH_LINES = int(os.environ.get("RETRIEVAL_LARGE_PATCH_LINES", "1000") or 1000)

# Backup / artifact filename patterns (see mission notes: *.bak, *.bak-*, *.orig,
# *.rej, *~, *_backup*, *backup[0-9]*, *.old).
BACKUP_PATTERNS = (
    "*.bak",
    "*.bak-*",
    "*.orig",
    "*.rej",
    "*~",
    "*_backup*",
    "*backup[0-9]*",
    "*.old",
)


def is_backup(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatch(name, p) for p in BACKUP_PATTERNS)


def line_count(path: str) -> int | None:
    """Line count of an existing file, or None if it cannot be resolved."""
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return None


def parse_root_map(values: list[str]) -> dict[str, str]:
    """Parse repeated 'corpus_id=path' arguments into a mapping."""
    mapping: dict[str, str] = {}
    for v in values:
        if "=" not in v:
            raise SystemExit(f"error: --root-map expects corpus_id=path, got {v!r}")
        k, v2 = v.split("=", 1)
        mapping[k] = v2
    return mapping


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", required=True, help="path to retrieval_logs.sqlite")
    ap.add_argument(
        "--corpora",
        nargs="*",
        default=["d-openaiprojects-llama-cpp", "dcfr-c060ca97"],
        help="corpora to isolate (default: GDN mission corpora)",
    )
    ap.add_argument(
        "--root-map",
        nargs="*",
        default=[],
        metavar="CORPUS_ID=PATH",
        help="corpus roots, one per 'corpus_id=path' (repeatable)",
    )
    ap.add_argument("--root", default=None, help="fallback root for every corpus")
    ap.add_argument(
        "--exact-set",
        action="store_true",
        help="match only requests whose corpora_json is exactly the requested set",
    )
    ap.add_argument(
        "--mission",
        default=None,
        help="keep only requests whose trace_json mission_id (the scope key "
        "used by cache.py/service.py) equals this value",
    )
    ap.add_argument("--since", default=None, help="keep only requests with r.ts >= this")
    ap.add_argument("--until", default=None, help="keep only requests with r.ts < this")
    ap.add_argument("--top", type=int, default=10, help="number of top selected paths to list")
    args = ap.parse_args()

    db = args.db
    if not Path(db).exists():
        print(f"error: no such DB: {db}", file=sys.stderr)
        return 1

    root_map = parse_root_map(args.root_map)
    corpora = sorted(args.corpora)
    n = len(corpora)

    # Request-level conditions. The corpora filter is any-of (or exact-set);
    # the mission filter reads the same mission key the cache scope uses
    # (trace.mission_id, see cache.py/service.py scope_key), and --since/--until
    # bound r.ts.
    conditions: list[str] = []
    req_params: list = []
    if args.exact_set:
        expected = "|".join(corpora)
        conditions.append(
            "(SELECT group_concat(value, '|') "
            "FROM (SELECT value FROM json_each(r.corpora_json) ORDER BY value)) = ?"
        )
        req_params.append(expected)
    else:
        placeholders = ",".join("?" for _ in range(n))
        conditions.append(
            f"EXISTS (SELECT 1 FROM json_each(r.corpora_json) WHERE value IN ({placeholders}))"
        )
        req_params.extend(corpora)
    if args.mission is not None:
        conditions.append("json_extract(r.trace_json, '$.mission_id') = ?")
        req_params.append(args.mission)
    if args.since is not None:
        conditions.append("r.ts >= ?")
        req_params.append(args.since)
    if args.until is not None:
        conditions.append("r.ts < ?")
        req_params.append(args.until)

    where = " AND ".join(conditions)
    sql = f"""
        SELECT c.corpus_id, c.path, c.token_count
        FROM retrieval_candidates c
        JOIN retrieval_requests r ON r.request_id = c.request_id
        WHERE c.selected = 1
          AND {where}
          AND c.corpus_id IN ({",".join("?" for _ in range(n))})
    """
    params = tuple(req_params) + tuple(corpora)

    req_sql = f"""
        SELECT COUNT(*)
        FROM retrieval_requests r
        WHERE {where}
    """

    con = sqlite3.connect(f"file:{db}?mode=ro")
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(sql, params).fetchall()
        n_requests = con.execute(req_sql, tuple(req_params)).fetchone()[0]
    finally:
        con.close()

    if not rows:
        print(f"No selected candidates found for corpora {corpora} in {db}.")
        print("(Check the --corpora value against the corpora_json stored in retrieval_requests.)")
        return 1

    def root_for(corpus_id: str) -> str | None:
        return root_map.get(corpus_id) or args.root

    def resolve(path: str, corpus_id: str) -> Path | None:
        root = root_for(corpus_id)
        if not root:
            return None
        return Path(root) / path

    total_fragments = len(rows)
    total_tokens = sum(r["token_count"] or 0 for r in rows)

    backup_rows = [r for r in rows if is_backup(r["path"])]
    backup_fragments = len(backup_rows)
    backup_tokens = sum(r["token_count"] or 0 for r in backup_rows)

    # .patch / .diff files: resolved per-corpus; > LARGE_PATCH_LINES bucket.
    patch_rows: list = []
    unknown_patch: Counter = Counter()
    for r in rows:
        name = r["path"].rsplit("/", 1)[-1]
        if not re.search(r"\.(patch|diff)$", name):
            continue
        target = resolve(r["path"], r["corpus_id"])
        n = line_count(str(target)) if target is not None else None
        if n is None:
            unknown_patch[r["path"]] += 1
        elif n > LARGE_PATCH_LINES:
            patch_rows.append(r)
    patch_fragments = len(patch_rows)
    patch_tokens = sum(r["token_count"] or 0 for r in patch_rows)

    def pct(num: int, den: int) -> str:
        return f"{100.0 * num / den:.1f}%" if den else "0.0%"

    path_counter: Counter = Counter(r["path"] for r in rows)

    print(f"DB: {db}")
    print(f"corpora: {corpora}")
    print(f"matched requests: {n_requests}")
    print(f"total selected fragments: {total_fragments}")
    print(f"total selected tokens:     {total_tokens}")
    print(
        f"(a) backup/artifact files: {backup_fragments} fragments, "
        f"{backup_tokens} tokens, share {pct(backup_fragments, total_fragments)} "
        f"(tokens {pct(backup_tokens, total_tokens)})"
    )
    print(
        f"(b) .patch/.diff >{LARGE_PATCH_LINES} lines: {patch_fragments} fragments, "
        f"{patch_tokens} tokens, share {pct(patch_fragments, total_fragments)} "
        f"(tokens {pct(patch_tokens, total_tokens)})"
    )
    if unknown_patch:
        print(
            f"    note: {sum(unknown_patch.values())} .patch/.diff fragment(s) could not be "
            f"resolved to a line count (reported as 'unknown'), excluded from (b):"
        )
        for path, cnt in unknown_patch.most_common():
            print(f"        {cnt:>4}  {path}  (unknown)")
    print(f"top-{args.top} selected paths (by fragment count):")
    for path, cnt in path_counter.most_common(args.top):
        print(f"    {cnt:>4}  {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
