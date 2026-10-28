"""A QA request as a run of the runner's list.

The QA scripts (scenarios, API checks, the smoke crawl) call no model, so a QA request never was a
run and the dashboard showed no QA at all. The runner records one run per request, role ``qa``,
started at the claim and finished with the result: the verdicts are in the output and in a
``qa_verdict`` event, the cost is 0.
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from .runs import RunManager
from .store import now

logger = logging.getLogger("claude_runner.qa_run_record")

QA_ROLE = "qa"
QA_PROVIDER = "qa"
QA_MODEL = "scripted"
FAILED_LISTED = 10
SCENARIO_MAX_CHARS = 120
REASON_MAX_CHARS = 400


def run_id_of(request_id: str) -> str:
    return "run_" + request_id


def _verdict_of(entry: str | dict[str, Any]) -> tuple[str, str]:
    if isinstance(entry, str):
        return entry, ""
    scenario = str(entry.get("scenario") or "")[:SCENARIO_MAX_CHARS]
    return str(entry.get("verdict") or "unknown"), scenario


def _reason_of(entry: dict[str, Any]) -> str:
    """Why a scenario did not pass: the blocker, the failed step, or the first browser error."""
    parts: list[str] = []
    if entry.get("blocked_reason"):
        parts.append(str(entry["blocked_reason"]))
    if entry.get("failed_step") is not None:
        parts.append(f"step {entry['failed_step']}")
    for key in ("failed_reason", "reason"):
        if entry.get(key):
            parts.append(str(entry[key]))
    for key in ("console_errors", "network_errors"):
        errors = entry.get(key) or []
        if errors:
            parts.append(f"{key.split('_')[0]}: {errors[0]}")
    return "; ".join(parts)[:REASON_MAX_CHARS]


def scenario_event(entry: str | dict[str, Any]) -> dict[str, Any]:
    """One event per verdict, so the run shows every scenario with its own result."""
    if isinstance(entry, str):
        return {"kind": "qa_scenario", "scenario": "", "layer": "", "verdict": entry, "reason": ""}
    event: dict[str, Any] = {
        "kind": "qa_scenario",
        "scenario": str(entry.get("scenario") or "")[:SCENARIO_MAX_CHARS],
        "layer": str(entry.get("layer") or ""),
        "verdict": str(entry.get("verdict") or "unknown"),
        "reason": _reason_of(entry),
    }
    if entry.get("minutes"):
        event["minutes"] = entry["minutes"]
    return event


def summarize(verdicts: list[str | dict[str, Any]], error: str) -> tuple[str, dict[str, Any]]:
    """The one-paragraph output of the run and the counts kept in its details."""
    counts: Counter[str] = Counter()
    failed: list[str] = []
    for entry in verdicts:
        verdict, scenario = _verdict_of(entry)
        counts[verdict] += 1
        if verdict == "fail" and scenario and len(failed) < FAILED_LISTED:
            failed.append(scenario)
    parts = [f"{count} {verdict}" for verdict, count in sorted(counts.items())]
    text = "QA verdicts: " + (", ".join(parts) if parts else "none")
    if failed:
        text += "\nFailed: " + "; ".join(failed)
    if error:
        text += "\nError: " + error
    return text, {"counts": dict(counts), "failed": failed, "error": error}


def start(manager: RunManager, request: dict[str, Any]) -> None:
    """Open the run when the worker claims the request; never raises (QA goes on without it)."""
    run_id = run_id_of(str(request["id"]))
    layers = ", ".join(request.get("layers") or []) or "all layers"
    prompt = (
        f"QA request {request['id']} of mission {request['mission_id']} "
        f"({request['mode']}; {layers})"
    )
    try:
        manager.store.create_run(
            run_id=run_id,
            session_id=str(request["id"]),
            claude_session_id=str(request["id"]),
            role=QA_ROLE,
            model=QA_MODEL,
            provider=QA_PROVIDER,
            cwd=str(request.get("working_directory") or ""),
            input=prompt,
            status="running",
            started_at=now(),
            owner_pid=manager.owner_pid,
        )
        manager.events.open(run_id)
        manager.events.add(run_id, {"kind": "prompt", "text": prompt})
    except Exception:  # noqa: BLE001 - logged; the QA request itself goes on
        logger.exception("QA run record for %s not opened", request.get("id"))


def finish(manager: RunManager, request: dict[str, Any]) -> None:
    """Close the run with the verdicts of the result. Never raises, like ``start``."""
    run_id = run_id_of(str(request["id"]))
    try:
        if manager.store.get_run(run_id) is None:
            start(manager, request)
        error = str(request.get("error") or "")
        output, details = summarize(list(request.get("verdicts") or []), error)
        failed = bool(error) or details["counts"].get("fail", 0) > 0
        status = "failed" if failed else "completed"
        manager.store.update_run(
            run_id,
            status=status,
            output=output,
            error_code="qa_failed" if failed else "",
            error_message=output if failed else "",
            cost_usd=0.0,
            num_turns=sum(details["counts"].values()),
            details=details,
            finished_at=now(),
        )
        for entry in request.get("verdicts") or []:
            manager.events.add(run_id, scenario_event(entry))
        verdict_event = {"kind": "qa_verdict", "role": QA_ROLE, "status": status, "summary": output}
        manager.events.add(run_id, verdict_event)
        manager.events.add(run_id, {"kind": "end", "status": status, "error_code": "", "error": ""})
    except Exception:  # noqa: BLE001 - logged; the QA result itself is already stored
        logger.exception("QA run record for %s not closed", request.get("id"))
