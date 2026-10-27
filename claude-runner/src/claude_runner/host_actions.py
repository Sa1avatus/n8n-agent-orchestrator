"""Host actions: the states, the request record and the whitelist of services the runner may act on.

No Docker and no network code here: the host executor applies a whitelisted command, and the routes
call this module to move requests. A request names a service and an action; both are looked up in
the loaded whitelist and never run as given. The whitelist is read from a JSON file; without one the
default below is used.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from claude_runner.store import now as utc_now

HOST_ACTIONS_FLAG = "HOST_ACTIONS"
MIN_SECONDS_BETWEEN_ACTIONS = 300
MAX_PENDING_HOURS = 1
MAX_REQUESTS = 200
MAX_REASON_CHARS = 500
MAX_MESSAGE_CHARS = 500
FINISHED_RETAIN_HOURS = 24
CLAIM_TIMEOUT_SECONDS = 30 * 60
CLAIM_TIMEOUT_MESSAGE = "claim timed out"

TIER_AUTO = "AUTO"
TIER_CONFIRM = "CONFIRM"
TIER_NOT_WHITELISTED = "NOT WHITELISTED"
TIERS = (TIER_AUTO, TIER_CONFIRM, TIER_NOT_WHITELISTED)
ACTIONS = ("recreate", "restart")
HOST_TRANSITIONS = frozenset({
    ("pending", "confirmed"),
    ("pending", "rejected"),
    ("pending", "expired"),
    ("confirmed", "running"),
    ("confirmed", "expired"),
    ("confirmed", "rejected"),
    ("running", "done"),
    ("running", "failed"),
})
FINISHED_STATES = frozenset({"done", "failed", "rejected", "expired"})
RUNNABLE_STATES = frozenset({"pending", "confirmed"})
BLOCKING_ROLES = frozenset({"worker", "reviewer", "qa"})


class HostActionTransitionError(ValueError):
    pass


class WhitelistError(ValueError):
    pass


@dataclass
class HostActionRequest:
    id: str
    action: str
    service: str
    reason: str
    tier: str
    state: str
    created_at: str
    updated_at: str
    requested_by: str
    run_id: str | None = None
    ok: bool | None = None
    message: str = ""


def host_actions_enabled(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return source.get(HOST_ACTIONS_FLAG, "").strip().lower() in ("1", "true")


def transition(request: HostActionRequest, target: str, at: str) -> None:
    if (request.state, target) not in HOST_TRANSITIONS:
        raise HostActionTransitionError(
            f"host action {request.id} cannot go from {request.state} to {target}"
        )
    request.state = target
    request.updated_at = at


@dataclass(frozen=True)
class Service:
    container: str
    tier: str


@dataclass(frozen=True)
class Whitelist:
    actions: dict[str, str]
    services: dict[str, Service]


DEFAULT_WHITELIST_DATA: dict[str, Any] = {
    "actions": {
        "recreate": "docker compose up -d --no-deps <service>",
        "restart": "docker restart <container>",
    },
    "services": {
        "ahawr-postgres": {"container": "ahawr-postgres", "tier": "NOT WHITELISTED"},
        "n8n": {"container": "n8n-autonomous-agents", "tier": "CONFIRM"},
        "ahawr-retrieval": {"container": "ahawr-retrieval", "tier": "NOT WHITELISTED"},
        "claude-runner": {"container": "claude-runner", "tier": "CONFIRM"},
        "ahawr-qa": {"container": "ahawr-qa", "tier": "AUTO"},
        "ahawr-dashboard": {"container": "ahawr-dashboard", "tier": "AUTO"},
        "litellm": {"container": "ahawr-litellm", "tier": "CONFIRM"},
    },
}


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise WhitelistError(f"{where} must be a non-empty string")
    return value


def _object(value: object, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WhitelistError(f"{where} must be an object")
    return value


def parse_whitelist(data: object, source: str = "default whitelist") -> Whitelist:
    root = _object(data, source)
    actions: dict[str, str] = {}
    for name, template in _object(root.get("actions"), f"{source}: 'actions'").items():
        if name not in ACTIONS:
            raise WhitelistError(
                f"{source}: unknown action {name!r}, expected one of {', '.join(ACTIONS)}"
            )
        actions[name] = _text(template, f"{source}: action {name!r}")
    services: dict[str, Service] = {}
    for name, entry in _object(root.get("services"), f"{source}: 'services'").items():
        fields = _object(entry, f"{source}: service {name!r}")
        tier = _text(fields.get("tier"), f"{source}: service {name!r} tier")
        if tier not in TIERS:
            raise WhitelistError(
                f"{source}: service {name!r} has unknown tier {tier!r}, expected one of "
                f"{', '.join(TIERS)}"
            )
        container = _text(fields.get("container"), f"{source}: service {name!r} container")
        services[name] = Service(container=container, tier=tier)
    return Whitelist(actions=actions, services=services)


DEFAULT_WHITELIST = parse_whitelist(DEFAULT_WHITELIST_DATA)


def load_whitelist(path: Path) -> Whitelist:
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        return DEFAULT_WHITELIST
    except json.JSONDecodeError as error:
        raise WhitelistError(f"whitelist {path}: not valid JSON: {error}") from error
    return parse_whitelist(data, str(path))


def validate_request(
    action: str, service: str, whitelist: Whitelist = DEFAULT_WHITELIST
) -> str:
    if action not in whitelist.actions:
        raise WhitelistError(f"unknown action {action!r}")
    entry = whitelist.services.get(service)
    if entry is None:
        raise WhitelistError(f"unknown service {service!r}")
    if entry.tier == TIER_NOT_WHITELISTED:
        raise WhitelistError(f"service {service!r} is not whitelisted")
    return entry.tier


class HostActionStoreFullError(RuntimeError):
    pass


def _older_than(stamp: str, at: datetime, span: timedelta) -> bool:
    return at - datetime.fromisoformat(stamp) > span


def _expired_state(request: HostActionRequest, at: datetime) -> str | None:
    if request.state == "pending" and _older_than(
        request.created_at, at, timedelta(hours=MAX_PENDING_HOURS)
    ):
        return "expired"
    if request.state == "running" and _older_than(
        request.updated_at, at, timedelta(seconds=CLAIM_TIMEOUT_SECONDS)
    ):
        return "failed"
    return None


@dataclass(frozen=True)
class ActiveRun:
    run_id: str
    role: str


@dataclass(frozen=True)
class HostActionFacts:
    active_runs: tuple[ActiveRun, ...]
    queue_at_boundary: bool
    queue_paused: bool
    last_action_at: Mapping[str, str]
    requester_run_id: str | None
    min_seconds_between_actions: int


def decide(request: HostActionRequest, facts: HostActionFacts, now: str) -> tuple[bool, str]:
    at = datetime.fromisoformat(now)
    if request.state == "expired" or _expired_state(request, at) == "expired":
        return False, "request has expired"
    if request.state not in RUNNABLE_STATES:
        return False, f"request is {request.state}, not waiting to run"
    if request.tier == TIER_CONFIRM and request.state != "confirmed":
        return False, "CONFIRM tier needs the operator to confirm the request first"
    if request.tier not in (TIER_AUTO, TIER_CONFIRM):
        return False, f"service {request.service!r} is not whitelisted"
    if (
        request.service == "claude-runner"
        and request.action == "recreate"
        and any(run.run_id == facts.requester_run_id for run in facts.active_runs)
    ):
        return False, "claude-runner recreate waits: the requesting run is still active"
    busy = next((run for run in facts.active_runs if run.role in BLOCKING_ROLES), None)
    if busy is not None:
        return False, f"{busy.role} run {busy.run_id} is active"
    if not (facts.queue_at_boundary or facts.queue_paused):
        return False, "queue is in the middle of a task: not at a task boundary and not paused"
    last = facts.last_action_at.get(request.service)
    if last is not None and at - datetime.fromisoformat(last) < timedelta(
        seconds=facts.min_seconds_between_actions
    ):
        return False, (
            f"{request.service!r} had an action less than "
            f"{facts.min_seconds_between_actions} seconds ago"
        )
    return True, "may run"


def _load(path: str) -> list[HostActionRequest]:
    try:
        with open(path, encoding="utf-8") as handle:
            rows = json.load(handle)
    except FileNotFoundError:
        return []
    return [HostActionRequest(**row) for row in rows]


class HostActionStore:
    def __init__(
        self,
        path: str,
        whitelist: Whitelist = DEFAULT_WHITELIST,
        clock: Callable[[], str] = utc_now,
    ) -> None:
        self.path = path
        self._whitelist = whitelist
        self._clock = clock
        self._lock = threading.Lock()
        self._requests = _load(path)

    def create(
        self, action: str, service: str, reason: str, requested_by: str
    ) -> HostActionRequest:
        tier = validate_request(action, service, self._whitelist)
        with self._lock:
            at = self._clock()
            self._expire(at)
            self._prune(datetime.fromisoformat(at), MAX_REQUESTS - 1)
            if len(self._requests) >= MAX_REQUESTS:
                raise HostActionStoreFullError(
                    f"host action store holds {MAX_REQUESTS} unfinished requests"
                )
            request = HostActionRequest(
                id=uuid.uuid4().hex,
                action=action,
                service=service,
                reason=reason[:MAX_REASON_CHARS],
                tier=tier,
                state="pending",
                created_at=at,
                updated_at=at,
                requested_by=requested_by,
            )
            self._requests.append(request)
            self._save()
            return request

    def get(self, request_id: str) -> HostActionRequest | None:
        with self._lock:
            self._expire(self._clock())
            return next((r for r in self._requests if r.id == request_id), None)

    def list_requests(self, state: str | None = None) -> list[HostActionRequest]:
        with self._lock:
            self._expire(self._clock())
            return [r for r in self._requests if state is None or r.state == state]

    def expire(self) -> None:
        with self._lock:
            self._expire(self._clock())

    def confirm(self, request_id: str) -> HostActionRequest:
        return self._move(request_id, "confirmed")

    def reject(self, request_id: str) -> HostActionRequest:
        return self._move(request_id, "rejected")

    def claim(self) -> HostActionRequest | None:
        with self._lock:
            at = self._clock()
            self._expire(at)
            if any(r.state == "running" for r in self._requests):
                return None
            request = next((r for r in self._requests if r.state == "confirmed"), None)
            if request is None:
                return None
            transition(request, "running", at)
            self._save()
            return request

    def report(self, request_id: str, ok: bool, message: str) -> HostActionRequest:
        with self._lock:
            at = self._clock()
            self._expire(at)
            request = self._require(request_id)
            transition(request, "done" if ok else "failed", at)
            request.ok = ok
            request.message = message[:MAX_MESSAGE_CHARS]
            self._save()
            return request

    def _move(self, request_id: str, target: str) -> HostActionRequest:
        with self._lock:
            at = self._clock()
            self._expire(at)
            request = self._require(request_id)
            transition(request, target, at)
            self._save()
            return request

    def _require(self, request_id: str) -> HostActionRequest:
        request = next((r for r in self._requests if r.id == request_id), None)
        if request is None:
            raise KeyError(f"unknown host action: {request_id}")
        return request

    def _expire(self, at: str) -> None:
        stamp = datetime.fromisoformat(at)
        moved = False
        for request in self._requests:
            target = _expired_state(request, stamp)
            if target is None:
                continue
            transition(request, target, at)
            if target == "failed":
                request.ok = False
                request.message = CLAIM_TIMEOUT_MESSAGE
            moved = True
        count = len(self._requests)
        self._prune(stamp, MAX_REQUESTS)
        if moved or len(self._requests) != count:
            self._save()

    def _prune(self, at: datetime, limit: int) -> None:
        retain = timedelta(hours=FINISHED_RETAIN_HOURS)
        kept = [
            r
            for r in self._requests
            if r.state not in FINISHED_STATES or not _older_than(r.updated_at, at, retain)
        ]
        excess = len(kept) - limit
        if excess > 0:
            finished = [r for r in kept if r.state in FINISHED_STATES]
            doomed = {r.id for r in finished[:excess]}
            kept = [r for r in kept if r.id not in doomed]
        self._requests = kept

    def _save(self) -> None:
        partial_path = self.path + ".part"
        with open(partial_path, "w", encoding="utf-8") as handle:
            json.dump([asdict(r) for r in self._requests], handle)
        os.replace(partial_path, self.path)
