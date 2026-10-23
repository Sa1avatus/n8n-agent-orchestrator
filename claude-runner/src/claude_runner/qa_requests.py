"""Queue of QA requests: the state machine and the JSON store behind the QA gate.

No network code here: the HTTP routes and the host worker call this module. A request
moves queued -> running -> done or error, or to timeout when nobody claims or finishes
it in time. The store is one JSON file, written atomically after every change.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

from claude_runner.config import QA_MAX_MINUTES
from claude_runner.roles_config import QA_DEFAULT_LAYERS, QA_LAYERS
from claude_runner.store import now as utc_now

QA_CLAIM_WAIT_MINUTES = 5
QA_RUN_BOUND_MINUTES = QA_MAX_MINUTES
QA_MAX_STORED_REQUESTS = 200
QA_UI_FILE_PATTERN = (
    r"(^|\/)(board[^\/]*\.(js|css)|[^\/]*\.html|board_api\.py|dashboard\.py|routes\.py)$"
    r"|(^|\/)routes\/"
)
REQUEST_MODES = ("final", "ui_tasks")
TERMINAL_STATES = frozenset({"done", "error", "timeout"})
QA_TRANSITIONS = frozenset({
    ("queued", "running"),
    ("queued", "timeout"),
    ("running", "done"),
    ("running", "error"),
    ("running", "timeout"),
})


class QaTransitionError(ValueError):
    pass


@dataclass
class QaRequest:
    id: str
    mission_id: str
    working_directory: str
    mode: str
    status: str
    nonce: str
    created_at: str
    claimed_at: str | None = None
    finished_at: str | None = None
    verdicts: list[str] = field(default_factory=list)
    error: str = ""
    layers: list[str] | None = None


def request_layers(request: QaRequest) -> list[str]:
    if request.layers:
        return list(request.layers)
    return list(QA_DEFAULT_LAYERS)


def transition(request: QaRequest, target: str, at: str) -> None:
    if (request.status, target) not in QA_TRANSITIONS:
        raise QaTransitionError(
            f"QA request {request.id} cannot go from {request.status} to {target}"
        )
    request.status = target
    if target == "running":
        request.claimed_at = at
    else:
        request.finished_at = at


def _is_expired(request: QaRequest, at: datetime) -> bool:
    if request.status == "queued":
        return at - datetime.fromisoformat(request.created_at) > timedelta(
            minutes=QA_CLAIM_WAIT_MINUTES
        )
    if request.status == "running" and request.claimed_at is not None:
        return at - datetime.fromisoformat(request.claimed_at) > timedelta(
            minutes=QA_RUN_BOUND_MINUTES
        )
    return False


def _load(path: str) -> list[QaRequest]:
    try:
        with open(path, encoding="utf-8") as handle:
            rows = json.load(handle)
    except FileNotFoundError:
        return []
    return [QaRequest(**row) for row in rows]


class QaRequestStore:
    def __init__(self, path: str, clock: Callable[[], str] = utc_now) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._requests = _load(path)

    def create(
        self,
        mission_id: str,
        working_directory: str,
        mode: str,
        nonce: str,
        layers: list[str] | None = None,
    ) -> QaRequest:
        if mode not in REQUEST_MODES:
            raise ValueError(f"unknown QA request mode: {mode}")
        for name in layers or []:
            if name not in QA_LAYERS:
                raise ValueError(f"unknown QA layer: {name}")
        with self._lock:
            self._expire(self._clock())
            for request in self._requests:
                if request.mission_id == mission_id and request.nonce == nonce:
                    return request
            request = QaRequest(
                id=uuid.uuid4().hex,
                mission_id=mission_id,
                working_directory=working_directory,
                mode=mode,
                status="queued",
                nonce=nonce,
                created_at=self._clock(),
                layers=list(layers) if layers else None,
            )
            self._requests.append(request)
            self._trim()
            self._save()
            return request

    def get(self, request_id: str) -> QaRequest | None:
        with self._lock:
            self._expire(self._clock())
            return next((r for r in self._requests if r.id == request_id), None)

    def list_requests(self, status: str | None = None) -> list[QaRequest]:
        with self._lock:
            self._expire(self._clock())
            return [r for r in self._requests if status is None or r.status == status]

    def claim(self, request_id: str) -> QaRequest:
        with self._lock:
            at = self._clock()
            self._expire(at)
            request = self._require(request_id)
            transition(request, "running", at)
            self._save()
            return request

    def finish(self, request_id: str, verdicts: Sequence[str], error: str) -> QaRequest:
        with self._lock:
            at = self._clock()
            self._expire(at)
            request = self._require(request_id)
            transition(request, "error" if error else "done", at)
            request.verdicts = list(verdicts)
            request.error = error
            self._save()
            return request

    def _require(self, request_id: str) -> QaRequest:
        request = next((r for r in self._requests if r.id == request_id), None)
        if request is None:
            raise KeyError(f"unknown QA request: {request_id}")
        return request

    def _expire(self, at: str) -> None:
        stamp = datetime.fromisoformat(at)
        expired = [r for r in self._requests if _is_expired(r, stamp)]
        for request in expired:
            transition(request, "timeout", at)
        if expired:
            self._save()

    def _trim(self) -> None:
        excess = len(self._requests) - QA_MAX_STORED_REQUESTS
        if excess <= 0:
            return
        order = sorted(
            range(len(self._requests)),
            key=lambda i: self._requests[i].status not in TERMINAL_STATES,
        )
        doomed = set(order[:excess])
        self._requests = [r for i, r in enumerate(self._requests) if i not in doomed]

    def request_dict(self, request: QaRequest) -> dict[str, Any]:
        data = asdict(request)
        if request.layers is None:
            data.pop("layers", None)
        return data

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump([self.request_dict(r) for r in self._requests], handle)
        os.replace(tmp, self.path)
