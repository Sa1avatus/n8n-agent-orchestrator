"""Hermes engine client (providers with ENGINE=hermes): the three calls of the Hermes gateway.

``start_run``, ``poll_run`` and ``cancel_run`` are the client functions; their endpoints and shapes
are those recorded in docs/hermes-engine.md (repo copy of ``Hermes_Run_Manager_v5.json`, not
verified against live). This module does not touch the store, the event log or
the process registry: the run manager keeps the session row and decides the retries.

Stdlib only (urllib). Reused helpers from sources.py: SourceError (the base of HermesError) and
``_iso`` for result timestamps; sources.py ``_check`` takes an httpx response and `_json_body` a
pydantic body, so neither fits a urllib response, and the error shape here mirrors ``_check`` with
the same text clip. The mapping of the payload to entries and failure classes is in
hermes_events.py. The env variable NAMES come from the profile;
their values are read at call time and never logged nor put in an exception message.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from .config import ProviderProfile
from .hermes_events import continue_fields, route_for
from .sources import SourceError, _iso

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "canceled"})
ERROR_TEXT_CHARS = 200  # same clip as sources.py _check


class HermesError(SourceError):
    """Network or gateway failure with the reason; the message never carries a secret value."""

    def __init__(self, reason: str, http_code: int | None = None) -> None:
        super().__init__(reason)
        self.http_code = http_code


def _env_value(var_name: str) -> str:
    """The value of the env variable named by the profile, read at call time."""
    value = os.environ.get(var_name, "")
    if not value:
        raise HermesError(f"Hermes: the env variable {var_name} is not set")
    return value


def _auth_headers(profile: ProviderProfile) -> dict[str, str]:
    return {"Authorization": f"Bearer {_env_value(profile.hermes_api_key_var)}"}


def _parsed_json(text: str) -> dict[str, Any]:
    # one json.loads here: no second parse helper
    try:
        body = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HermesError(f"Hermes response is not JSON: {text[:ERROR_TEXT_CHARS]}") from exc
    if not isinstance(body, dict):
        raise HermesError(f"Hermes response is not an object: {text[:ERROR_TEXT_CHARS]}")
    return body


def _send(
    profile: ProviderProfile,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    headers: dict[str, str],
) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{_env_value(profile.hermes_base_url_var).rstrip('/')}{path}",
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers=headers,
        method=method,
    )
    # shortcut: one read-time timeout for every call, the start timeout from the profile;
    # upgrade to per-call timeouts when the gateway needs different ones
    timeout_s = profile.hermes_start_timeout_ms / 1000
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as resp:
            status, text = resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status, text = exc.code, exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise HermesError(f"Hermes call failed: {exc.reason!r}") from exc
    parsed = _parsed_json(text)
    if status >= 400:
        raise HermesError(f"Hermes: HTTP {status} {text[:ERROR_TEXT_CHARS]}", status)
    return parsed


def start_run(
    profile: ProviderProfile,
    prompt: str,
    model: str,
    session_id: str = "",
    cwd: str = "",
    route: str = "",
) -> tuple[str, str]:
    """POST /v1/runs (Start / Resume Run): the body fields are only set when present.

    `route` names the follow-up run (Evaluate Status); it decides the continue fields, so the
    stored session id is sent only when the route resumes the session.

    Returns (run_id, session_id); the session key names the gateway shape is UNKNOWN, so the
    response is read with the same fallbacks as Capture Run Response.
    """
    if not (prompt or model):
        raise HermesError("Hermes start: model and the input are required")
    body: dict[str, Any] = {"model": model, "provider": profile.name}
    body.update(continue_fields(session_id, route, prompt))
    if cwd:
        body["working_directory"] = cwd
    headers = _auth_headers(profile)
    if session_id:
        headers["X-Hermes-Session-Id"] = session_id
    if cwd:
        headers["X-Hermes-Working-Directory"] = cwd
    parsed = _send(profile, "POST", "/v1/runs", body, headers)
    # shortcut: the gateway's own field names are UNKNOWN, the fallbacks follow the repo copy;
    # upgrade when a live export fixes the shape
    session_part = parsed.get("session") or {}
    session = (
        parsed.get("session_id") or parsed.get("sessionId") or session_part.get("session_id")
        or session_part.get("id") or session_id
    )
    run_id = str(parsed.get("run_id") or "")
    if not run_id:
        raise HermesError("Hermes start returned no run_id")
    return run_id, str(session or "")


def _result_fields(body: dict[str, Any], run_id: str, poll_count: int) -> dict[str, Any]:
    output = body.get("output") or body.get("response") or body.get("content") or ""
    error = body.get("error") or body.get("message") or body.get("details") or body.get("detail")
    status = str(body.get("status") or body.get("final_status") or "")
    return {
        "run_id": run_id,
        "status": status,
        "output": str(output),
        "error": str(error) if error else "",
        "poll_count": poll_count,
        "finished_at": _iso(body.get("finished_at") or body.get("ended_at")),
    }


def poll_run(profile: ProviderProfile, run_id: str) -> dict[str, Any]:
    """GET /v1/runs/{run_id} (Get Run Status): bounded by max_polls and by the run deadline;
    returns on the first terminal status, otherwise the deadline result (timeout).
    """
    # shortcut: the repo-copy poll node sends no headers; the client keeps Bearer auth, the
    # gateway always requires it
    deadline = time.monotonic() + profile.hermes_run_timeout_ms / 1000
    poll_count = 0
    last: dict[str, Any] | None = None
    while poll_count < profile.hermes_max_polls and time.monotonic() < deadline:
        poll_count += 1
        body = _send(profile, "GET", f"/v1/runs/{run_id}", None, _auth_headers(profile))
        status = str(body.get("status") or "")
        if status in TERMINAL_STATUSES:
            result = _result_fields(body, run_id, poll_count)
            result["route"] = route_for(body, 200) if status == "failed" else status
            return result
        last = body
        time.sleep(profile.hermes_poll_interval_ms / 1000)
    result = _result_fields(last or {}, run_id, poll_count)
    result["route"] = "timeout"
    return result


def cancel_run(profile: ProviderProfile, run_id: str) -> dict[str, Any]:
    """Stop a run on the gateway. The stop endpoint is UNKNOWN (the repo copy has no stop node);
    the runner's cancel route shape is followed until a live export names the gateway one.

    shortcut: endpoint is the runner route, upgrade when the Hermes stop path is verified.
    """
    parsed = _send(profile, "POST", f"/v1/runs/{run_id}/cancel", None, _auth_headers(profile))
    if not parsed.get("run_id"):
        raise HermesError("Hermes cancel returned no run_id")
    return parsed
