"""Start and poll the run of the ``qa_agent`` role.

The run is started through ``POST /v1/runs`` with role ``qa_agent`` and the prompt
of the agent (T009), without a model or provider in the body: the runner
resolves both from ``roles_json``. The run is polled with a bounded number of
polls and a delay; when it ends, its output is returned as the final text. A
timeout or a failed run returns a status the caller turns into a FAIL for every
item. The HTTP calls reuse the QA worker's runner helper
(``qa_worker._call``), so there is no second fetch helper.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from ahawr_runner import qa_worker
from ahawr_runner.config import QA_MAX_MINUTES
from ahawr_runner.roles_config import split_model

logger = logging.getLogger(__name__)

# The agent run is bounded by the role's max_minutes (default 20): one poll
# per minute, one poll per max_minutes.
DEFAULT_MAX_POLLS = QA_MAX_MINUTES
DEFAULT_POLL_SECONDS = 60.0
QA_AGENT_ROLE = "qa_agent"


@dataclass(frozen=True)
class AgentOutcome:
    """The agent run result: ``completed`` with the final text, or a timeout or
    failure the caller turns into a FAIL for every item."""

    status: str
    final_text: str = ""
    reason: str = ""


def run_agent(
    prompt: str,
    working_directory: str,
    max_polls: int = DEFAULT_MAX_POLLS,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
) -> AgentOutcome:
    """Start the qa_agent run and poll it until it ends.

    The first body carries only the role, the prompt and the working directory: the
    model and provider come from ``roles_json``. When that run fails (not on a
    timeout), the run is started again for each ``options.fallback`` model of the
    role in order, with the model and provider named in the body. On completion the
    run's output is returned as ``final_text``; on a timeout or a failed run
    ``final_text`` stays empty and ``reason`` says why.
    """
    body = {
        "input": prompt,
        "role": QA_AGENT_ROLE,
        "working_directory": working_directory,
    }
    outcome = _run_once(body, max_polls, poll_seconds)
    if outcome.status != "failed":
        return outcome
    for spec in _fallback_models():
        provider, name = split_model(spec)
        logger.warning("qa_agent failed (%s); retrying on %s", outcome.reason, spec)
        outcome = _run_once({**body, "provider": provider, "model": name}, max_polls, poll_seconds)
        if outcome.status != "failed":
            return outcome
    return outcome


def _fallback_models() -> list[str]:
    """The role's fallback models from the runner; empty when it cannot say."""
    try:
        answer = qa_worker._call("GET", "/v1/qa/agent-fallbacks")
    except qa_worker.RunnerError as exc:
        logger.warning("qa_agent fallbacks not read: %s", exc)
        return []
    models = answer.get("fallbacks") if isinstance(answer, dict) else None
    return [str(item) for item in models] if isinstance(models, list) else []


def _run_once(body: dict[str, Any], max_polls: int, poll_seconds: float) -> AgentOutcome:
    try:
        run = qa_worker._call("POST", "/v1/runs", body)
    except qa_worker.RunnerError as exc:
        return AgentOutcome("failed", reason=f"the run could not be started: {exc}")
    run_id = str(run.get("run_id", ""))
    status = ""
    for _ in range(max_polls):
        try:
            view = qa_worker._call("GET", f"/v1/runs/{run_id}")
        except qa_worker.RunnerError as exc:
            return AgentOutcome("failed", reason=f"the run could not be polled: {exc}")
        status = str(view.get("status", ""))
        if status == "completed":
            return AgentOutcome("completed", str(view.get("output", "")))
        if status not in ("queued", "running"):
            return AgentOutcome("failed", reason=_failure_reason(view))
        time.sleep(poll_seconds)
    return AgentOutcome("timeout", reason=f"still {status} after {max_polls} polls")


def _failure_reason(view: dict[str, Any]) -> str:
    """The failed run's reason from its error field, or a plain sentence."""
    error = view.get("error")
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return "the run ended failed"
