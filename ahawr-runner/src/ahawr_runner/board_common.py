"""Shared helpers for the board route modules and the Postgres stores.

``board_api`` and ``board_ops_api`` both build the same 503/4xx envelopes from
the store's own ``status()`` reason, and both answer the shared exception types
with the same handlers; this is the single body they import, so a change here
reaches both routes.  ``board_pg`` uses the shared ``_iso`` timestamp formatter
too, keeping the behaviour pinned by the store tests.  The module imports
nothing from the ``board_*_api`` or store modules it serves (the shared module
stays acyclic).
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from .board import ConflictError


def _error(exc: Exception, code: str, status: int) -> JSONResponse:
    """The runner's error envelope for board errors."""
    return JSONResponse(
        status_code=status,
        content={
            "status": "failed",
            "error": {"code": code, "message": str(exc)},
            "code": code,
            "status_code": status,
        },
    )


def _unavailable(store: Any) -> dict[str, Any] | None:
    """503 with the store's own reason when the backend is unreachable
    (the reason never carries the DSN)."""
    if hasattr(store, "status"):
        status, reason = store.status()
        if status == "unavailable":
            return {"code": "unavailable", "reason": reason}
    return None


def _on_conflict(_: Request, exc: ConflictError) -> JSONResponse:
    return _error(exc, "conflict", 409)


def _on_missing(_: Request, exc: KeyError) -> JSONResponse:
    return _error(exc, "not_found", 404)


def _on_value(_: Request, exc: ValueError) -> JSONResponse:
    return _error(exc, "bad_request", 400)


def _on_runtime(_: Request, exc: RuntimeError) -> JSONResponse:
    return _error(exc, "unavailable", 503)


def _iso(value: Any) -> str | None:
    """ISO-8601 string of a timestamp column (None stays None)."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)
