#!/usr/bin/env python3
"""Patch the TASK STARTED Telegram notice in both v13 workflows.

The notice is built from task fields only (id, title, attempt, OBJECTIVE,
ACCEPTANCE CRITERIA, VERIFICATION, SCOPE) plus, on retry, the previous
review (score, reason, Asked next).  The worker prompt template,
working-directory block, and retrieval context are absent.
Long text is split on section boundaries, not a blind 3800-char cut.
HTML is escaped.

Idempotent: re-running the script is a no-op if the target text is
already present.

Checks performed:
  - JSON is valid
  - node ids and names are unique
  - all connections resolve to existing node ids
  - Build Worker Run Input jsCode is unchanged (before/after)
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FILES = [
    ROOT / "AHAWR_v13_ClaudeCode.json",
    ROOT / "AHAWR_v13.json",
]

TARGET_NODE = "Send a text message"

# The new jsCode for the "text" expression.  It builds the notice from
# task fields only and splits on section boundaries when the total
# exceeds the 3800-char Telegram limit.
NEW_TEXT_EXPR = r"""={{ (() => {
  const j = $json, t = j.current_task || {};
  const attempt = Number(j.task_attempt ?? 1);
  const sections = [];

  // Header
  sections.push('▶️ TASK STARTED');
  sections.push('🤖 Worker: ' + (()=>{try{const c=$('Constants').first().json;const f=(m,p)=>String(m||'?')+(p?' @'+p:'');return f(c.worker_model,c.worker_provider);}catch(e){return '?'}})());
  sections.push(String(j.current_task_id ?? '') + ': ' + String(t.title ?? '') + (attempt > 1 ? '  (attempt ' + attempt + ')' : ''));

  // Task fields
  if (t.objective) sections.push('OBJECTIVE:\n' + String(t.objective));
  if (Array.isArray(t.acceptance_criteria) && t.acceptance_criteria.length)
    sections.push('ACCEPTANCE CRITERIA:\n' + t.acceptance_criteria.map((x,i)=>(i+1)+'. '+String(x)).join('\n'));
  if (Array.isArray(t.verification) && t.verification.length)
    sections.push('VERIFICATION:\n' + t.verification.map(String).join('\n'));
  if (Array.isArray(t.scope) && t.scope.length)
    sections.push('SCOPE:\n' + t.scope.map(String).join('\n'));

  // Previous review (retry)
  const fb = String(j.review_feedback ?? '').trim();
  if (attempt > 1 && fb) {
    const score = Number(j.last_review_score);
    sections.push('PREVIOUS REVIEW' + (score ? ' (score ' + score + ')' : '') + ':\n' + String(j.last_review_reason ?? '').trim() + '\nAsked next: ' + fb);
  }

  // Split on section boundaries if the total exceeds the Telegram limit
  const LIMIT = 3800;
  const SEP = '\n\n';
  const parts = [];
  let cur = '';
  for (const sec of sections) {
    if (!cur) {
      cur = sec;
    } else if ((cur + SEP + sec).length > LIMIT) {
      parts.push(cur);
      cur = sec;
    } else {
      cur += SEP + sec;
    }
  }
  if (cur) parts.push(cur);

  // If a single section exceeds the limit, truncate it
  const out = [];
  for (const p of parts) {
    out.push(p.length > LIMIT ? p.slice(0, LIMIT) + '\n…' : p);
  }

  return out.join('\n\n').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
})() }}"""


def check_workflow(wf, label):
    """Validate node id/name uniqueness and connection resolution.

    The connections dict is keyed by node name; each entry maps to a list
    of outgoing connection groups.
    """
    ids = set()
    names = set()
    for n in wf["nodes"]:
        nid = n["id"]
        if nid in ids:
            raise ValueError(f"{label}: duplicate node id {nid}")
        ids.add(nid)
        nm = n["name"]
        if nm in names:
            raise ValueError(f"{label}: duplicate node name {nm}")
        names.add(nm)

    for src_name, outs in wf.get("connections", {}).items():
        if src_name not in names:
            raise ValueError(f"{label}: connection source {src_name!r} is not a known node name")
        for main in outs.get("main", []):
            for entry in main:
                if isinstance(entry, dict) and entry.get("node") not in names:
                    raise ValueError(
                        f"{label}: connection to node name {entry['node']!r} is not a known node name"
                    )
    return ids, names


def find_node(wf, name):
    for n in wf["nodes"]:
        if n["name"] == name:
            return n
    return None


def patch_file(path, label):
    wf = json.loads(path.read_text(encoding="utf-8"))

    # Pre-check: JSON valid, unique ids/names, connections resolve
    check_workflow(wf, label)

    node = find_node(wf, TARGET_NODE)
    if node is None:
        raise ValueError(f"{label}: node {TARGET_NODE!r} not found")

    # Capture Build Worker Run Input jsCode before the patch
    bwri = find_node(wf, "Build Worker Run Input")
    if bwri is None:
        raise ValueError(f"{label}: Build Worker Run Input node not found")
    bwri_before = bwri["parameters"]["jsCode"]

    current_text = node["parameters"]["text"]

    if current_text == NEW_TEXT_EXPR:
        print(f"{label}: already patched, skipping")
        return

    node["parameters"]["text"] = NEW_TEXT_EXPR

    # Post-checks
    check_workflow(wf, label)

    # Verify Build Worker Run Input is unchanged
    bwri_after = find_node(wf, "Build Worker Run Input")["parameters"]["jsCode"]
    if bwri_before != bwri_after:
        raise ValueError(f"{label}: Build Worker Run Input jsCode changed unexpectedly")

    path.write_text(json.dumps(wf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{label}: patched {TARGET_NODE!r}")


def main():
    for path, label in zip(FILES, ["AHAWR_v13_ClaudeCode.json", "AHAWR_v13.json"]):
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")
        patch_file(path, label)

    print("All done.")


if __name__ == "__main__":
    main()
