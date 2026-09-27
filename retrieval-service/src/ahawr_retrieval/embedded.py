"""Command-line execution of the service contract: one JSON request in, one JSON response out.

``ahawr-retrieval exec '<json or base64 json>'`` runs retrieve/index/invalidate/health without
HTTP, e.g. ``docker compose exec ahawr-retrieval ahawr-retrieval exec '{"action":"health"}'``
for scripting and debugging. AHAWR itself uses the HTTP API of the ``ahawr-retrieval``
container. Several processes may share the on-disk index (SQLite WAL, busy timeout).

Payload::

    {"action": "retrieve" | "index" | "invalidate" | "health",
     "request": {...},                       # RetrieveRequest / IndexRequest / InvalidateRequest
     "bootstrap": [{"corpus_id": "...", "root": "/workspace"}]}   # optional, retrieve only

Result: ``{"ok": true, "action": ..., "response": {...}}`` or
``{"ok": false, "action": ..., "error": "...", "status_code": 4xx|5xx}``; the command always
exits 0.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from pydantic import ValidationError

from .config import Settings
from .indexer import IndexingError
from .models import IndexRequest, InvalidateRequest, RetrieveRequest
from .service import RetrievalError, RetrievalService


def decode_payload(token: str) -> dict[str, Any]:
    """Accept base64 JSON (n8n), raw JSON, with or without surrounding shell quotes."""
    text = token.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    if not text.startswith("{"):
        try:
            text = base64.b64decode(text, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValueError(f"payload is neither JSON nor base64 JSON: {exc}") from exc
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("payload must be a JSON object")
    return data


def execute(payload: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    action = str(payload.get("action", "retrieve"))
    result: dict[str, Any] = {"ok": False, "action": action}
    try:
        service = RetrievalService(settings or Settings.from_env())
    except Exception as exc:  # misconfiguration must still produce a JSON answer
        return {**result, "error": f"service init failed: {exc}", "status_code": 500}
    try:
        if action == "health":
            response: Any = service.health()
        elif action == "index":
            response = service.index(IndexRequest.model_validate(payload.get("request", {})))
        elif action == "invalidate":
            response = service.invalidate(
                InvalidateRequest.model_validate(payload.get("request", {}))
            )
        elif action == "retrieve":
            request = RetrieveRequest.model_validate(payload.get("request", {}))
            result["bootstrapped"] = _bootstrap(service, payload.get("bootstrap") or [])
            response = service.retrieve(request)
        else:
            return {**result, "error": f"unknown action {action!r}", "status_code": 400}
    except ValidationError as exc:
        return {
            **result,
            "error": exc.errors(include_url=False, include_input=False),
            "status_code": 422,
        }
    except RetrievalError as exc:
        return {**result, "error": str(exc), "status_code": exc.status_code}
    except IndexingError as exc:
        return {**result, "error": str(exc), "status_code": 400}
    except Exception as exc:  # fail-open contract: report, never crash the workflow
        return {**result, "error": f"{type(exc).__name__}: {exc}", "status_code": 500}
    finally:
        service.close()
    if hasattr(response, "model_dump"):
        response = response.model_dump(mode="json")
    return {**result, "ok": True, "response": response}


def _bootstrap(service: RetrievalService, specs: list[Any]) -> list[str]:
    """Index corpora that do not exist yet (first call after deployment)."""
    created: list[str] = []
    for spec in specs:
        if not isinstance(spec, dict) or not spec.get("corpus_id") or not spec.get("root"):
            continue
        corpus = service.store.get_corpus(str(spec["corpus_id"]))
        if corpus is not None and corpus.root:
            continue
        service.index(IndexRequest(corpus_id=str(spec["corpus_id"]), root=str(spec["root"])))
        created.append(str(spec["corpus_id"]))
    return created
