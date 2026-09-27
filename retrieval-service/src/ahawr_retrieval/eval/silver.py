"""Silver dataset builder: historical AHAWR tasks as weak ground truth.

A task becomes a silver case only when it has an accepted attempt (Reviewer ``pass``) and at
least one relevant file that still exists in the corpus. Relevant files come, in order of
preference, from an explicit changes map (e.g. ``git diff --name-only`` per task), or from paths
named in the accepted Worker output and the task scope. All judgments are file-level, grade 1,
and the case is marked ``weak``: Reviewer acceptance is observed evidence, not proof.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..chunking import classify_path
from ..indexer import is_excluded
from ..text import extract_paths
from .ahawr_metrics import review_status

MAX_WORKER_OUTPUT = 4000


def _plans(state_rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    plans: dict[str, dict[str, dict[str, Any]]] = {}
    for row in state_rows:
        raw = row.get("plan_json") or row.get("failure_plan_json")
        if not raw:
            continue
        try:
            plan = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            continue
        tasks = plan.get("tasks", []) if isinstance(plan, dict) else plan
        plans[str(row.get("state_key", ""))] = {
            str(t.get("id")): t for t in tasks if isinstance(t, dict) and t.get("id")
        }
    return plans


def _existing(root: Path, candidates: list[str]) -> list[str]:
    found: dict[str, None] = {}
    for candidate in candidates:
        rel = candidate.replace("\\", "/").strip().lstrip("./")
        if not rel or ".." in rel.split("/") or is_excluded(rel) or classify_path(rel) is None:
            continue
        if (root / rel).is_file():
            found.setdefault(rel, None)
    return list(found)


def build_silver(
    attempts: list[dict[str, Any]],
    state_rows: list[dict[str, Any]],
    corpus_id: str,
    root: str | Path,
    changes: dict[str, list[str]] | None = None,
    dataset_id: str = "ahawr-silver",
    include_reviewer: bool = True,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    plans = _plans(state_rows)
    accepted: dict[tuple[str, str], dict[str, Any]] = {}
    worker_outputs: dict[tuple[str, str, int], str] = {}
    for row in attempts:
        key = (str(row.get("state_key", "")), str(row.get("task_id", "")))
        attempt = int(float(row.get("task_attempt") or 1))
        if row.get("event_type") == "worker_completed":
            worker_outputs[(*key, attempt)] = str(row.get("worker_output") or "")
        if (
            row.get("event_type") == "reviewer_completed"
            and review_status(row.get("reviewer_output")) == "pass"
        ):
            accepted[key] = {"attempt": attempt}
    tasks: list[dict[str, Any]] = []
    skipped: dict[str, int] = {"no_plan": 0, "no_relevant_files": 0}
    for (state_key, task_id), info in sorted(accepted.items()):
        plan_task = plans.get(state_key, {}).get(task_id)
        if plan_task is None:
            skipped["no_plan"] += 1
            continue
        worker_output = worker_outputs.get((state_key, task_id, info["attempt"]), "")
        change_key = f"{state_key}/{task_id}"
        if changes and change_key in changes:
            evidence = "changes_map"
            paths = _existing(root_path, changes[change_key])
        else:
            evidence = "worker_output_and_scope"
            scope = [str(s) for s in plan_task.get("scope", [])]
            paths = _existing(
                root_path,
                extract_paths(worker_output) + [p for s in scope for p in extract_paths(s)],
            )
        if not paths:
            skipped["no_relevant_files"] += 1
            continue
        task_fields = {
            "title": plan_task.get("title", ""),
            "objective": plan_task.get("objective", plan_task.get("instructions", "")),
            "acceptance_criteria": plan_task.get("acceptance_criteria", []),
            "verification": plan_task.get("verification", []),
            "scope": plan_task.get("scope", []),
        }
        relevant = [{"path": p, "grade": 1} for p in paths]
        note = (
            f"silver from {state_key}; accepted at attempt {info['attempt']}; evidence: "
            f"{evidence}. Reviewer pass is observed evidence, not proven correctness."
        )
        tasks.append(
            {
                "id": f"S-{state_key}-{task_id}",
                "profile": "worker",
                "task": task_fields,
                "relevant": relevant,
                "weak": True,
                "tags": ["silver", evidence],
                "notes": note,
            }
        )
        if include_reviewer:
            tasks.append(
                {
                    "id": f"S-{state_key}-{task_id}-review",
                    "profile": "reviewer",
                    "task": task_fields,
                    "review": {
                        "worker_output": worker_output[:MAX_WORKER_OUTPUT],
                        "changed_files": paths,
                    },
                    "relevant": relevant,
                    "weak": True,
                    "tags": ["silver", evidence, "reviewer"],
                    "notes": note,
                }
            )
    return {
        "dataset_id": dataset_id,
        "version": "1",
        "kind": "silver",
        "description": "Historical AHAWR tasks with accepted solutions (weak ground truth).",
        "provenance": {
            "generator": "ahawr-retrieval-eval build-silver",
            "skipped": skipped,
            "accepted_tasks": len(accepted),
        },
        "corpora": [{"corpus_id": corpus_id, "root": str(root_path)}],
        "tasks": tasks,
    }
