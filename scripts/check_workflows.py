#!/usr/bin/env python3
"""Validate the AHAWR workflow files.

Checks, for each file:
  - the JSON parses (json valid);
  - node ids are unique and node names are unique;
  - every connection source and target points to an existing node;
  - no telegram bot token pattern and no "Bearer " appear anywhere in the file.

Usage: python scripts/check_workflows.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

WORKFLOW_FILES = [
    "AHAWR_v13_ClaudeCode.json",
    "AHAWR_v13.json",
    "AHAWR_Status_Bot.json",
]

# Shape of a Telegram bot token (same idiom as scripts/patch_status_bot_control.py):
# a run of >=15 alphanumerics, a dash, then >=10 alphanumerics/underscores.
# This matches a real bot token and rejects UUIDs, node ids, hex hashes,
# and the REDACTED-BOT-TOKEN placeholder.
TOKEN_RE = re.compile(r"[0-9A-Za-z]{15,}\s*-\s*[0-9A-Za-z_]{10,}")


def collect_nodes(wf, label):
    """Return (ids set, names set); raise on duplicates."""
    ids = set()
    names = set()
    for n in wf.get("nodes", []):
        nid = n.get("id")
        nm = n.get("name")
        if not isinstance(nid, str) or not nid:
            raise ValueError("%s: node missing 'id'" % label)
        if nid in ids:
            raise ValueError("%s: duplicate node id %s" % (label, nid))
        ids.add(nid)
        if not isinstance(nm, str) or not nm:
            raise ValueError("%s: node missing 'name'" % label)
        if nm in names:
            raise ValueError("%s: duplicate node name %r" % (label, nm))
        names.add(nm)
    return ids, names


def check_connections(wf, names, label):
    """Every connection source and target must be a known node name."""
    for src, outs in wf.get("connections", {}).items():
        if src not in names:
            raise ValueError("%s: connection source %r is not a known node" % (label, src))
        for main in outs.get("main", []):
            for entry in main:
                if not isinstance(entry, dict):
                    continue
                target = entry.get("node")
                if not target or target not in names:
                    raise ValueError(
                        "%s: connection from %r to %r does not point to a known node"
                        % (label, src, target)
                    )


def check_no_secrets(label, raw):
    """Fail if a telegram token pattern or 'Bearer ' appears in the file."""
    if "Bearer " in raw:
        raise ValueError("%s: 'Bearer ' found in file" % label)
    if TOKEN_RE.search(raw):
        raise ValueError("%s: telegram bot token pattern found in file" % label)


def validate_file(path, label):
    raw = path.read_text(encoding="utf-8")
    try:
        wf = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError("%s: JSON parse error: %s" % (label, e))
    if not isinstance(wf, dict):
        raise ValueError("%s: top level is not a JSON object" % label)

    ids, names = collect_nodes(wf, label)
    check_connections(wf, names, label)
    check_no_secrets(label, raw)

    print("%-40s OK  (%d nodes, %d connections, no token/key)"
          % (label, len(ids), len(wf.get("connections", {}))))


def main():
    failures = 0
    for name in WORKFLOW_FILES:
        path = ROOT / name
        if not path.exists():
            print("%-40s FAIL (file not found: %s)" % (name, path))
            failures += 1
            continue
        try:
            validate_file(path, name)
        except ValueError as e:
            print("%-40s FAIL  %s" % (name, e))
            failures += 1

    if failures:
        print("\n%d workflow file(s) failed validation." % failures)
        sys.exit(1)
    print("\nAll %d workflow files validated successfully." % len(WORKFLOW_FILES))


if __name__ == "__main__":
    main()
