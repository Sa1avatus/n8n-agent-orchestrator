from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar

LAYER = "api"
DASHBOARD_HEADER = "X-AHAWR-Dashboard"
REQUEST_TIMEOUT_SECONDS = 10.0
MUTATION_STATUS = 403
# The header guard answers 403 before routing, so any placeholder value
# works (dashboard.py:143-152).
PLACEHOLDER = "qa"
PATH_PARAMETER = re.compile(r"\{[^}]+\}")

T = TypeVar("T")


class RoutesError(ValueError):
    pass


@dataclass(frozen=True)
class ApiResponse:
    status_code: int
    body: str


class ApiClient(Protocol):
    def request(
        self, method: str, path: str, *, headers: Mapping[str, str], timeout_seconds: float
    ) -> ApiResponse: ...


@dataclass(frozen=True)
class GetRoute:
    path: str
    allowed_statuses: frozenset[int]
    non_empty: bool
    url_fields: tuple[str, ...]


@dataclass(frozen=True)
class Mutation:
    method: str
    path: str


@dataclass(frozen=True)
class ApiRoutes:
    get_routes: tuple[GetRoute, ...]
    mutations: tuple[Mutation, ...]


def load_routes(path: Path) -> ApiRoutes:
    import yaml  # the 'qa' extra, read the same way as qa_regression.load_profiles

    try:
        data: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RoutesError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RoutesError(f"{path}: expected a mapping at the top level")
    gets = tuple(_get_route(entry) for entry in _entries(data, "get_routes"))
    mutations = tuple(
        Mutation(method=_field(entry, "method", str), path=_field(entry, "path", str))
        for key in ("board_mutations", "dashboard_mutations")
        for entry in _entries(data, key)
    )
    return ApiRoutes(get_routes=gets, mutations=mutations)


def _entries(data: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    entries = data.get(key)
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise RoutesError(f"{key}: expected a list of mappings")
    return entries


def _get_route(entry: Mapping[str, Any]) -> GetRoute:
    statuses = _field(entry, "allowed_statuses", list)
    if not all(isinstance(code, int) for code in statuses):
        raise RoutesError("allowed_statuses: expected a list of integers")
    fields = _field(entry, "url_fields", list)
    if not all(isinstance(name, str) for name in fields):
        raise RoutesError("url_fields: expected a list of strings")
    return GetRoute(
        path=_field(entry, "path", str),
        allowed_statuses=frozenset(statuses),
        non_empty=_field(entry, "non_empty", bool),
        url_fields=tuple(fields),
    )


def _field(entry: Mapping[str, Any], key: str, kind: type[T]) -> T:
    value = entry.get(key)
    if not isinstance(value, kind):
        raise RoutesError(f"{key}: expected {kind.__name__}, got {value!r}")
    return value


def check_routes(
    client: ApiClient, routes: ApiRoutes, *, blocked_reason: str | None = None
) -> list[dict[str, Any]]:
    if blocked_reason is not None:
        return [_verdict(LAYER, "blocked", blocked_reason)]
    results = [_check_get(client, route) for route in routes.get_routes]
    results.extend(_check_mutation(client, mutation) for mutation in routes.mutations)
    return results


def _check_get(client: ApiClient, route: GetRoute) -> dict[str, Any]:
    label = f"GET {route.path}"
    try:
        response = client.request(
            "GET", route.path, headers={}, timeout_seconds=REQUEST_TIMEOUT_SECONDS
        )
    except OSError as exc:
        return _verdict(label, "blocked", f"connection failed ({type(exc).__name__})")
    problem = _get_problem(route, response)
    if problem is None:
        return _verdict(label, "pass")
    return _verdict(label, "fail", problem)


def _get_problem(route: GetRoute, response: ApiResponse) -> str | None:
    if response.status_code >= 500:
        return f"status {response.status_code} is a server error"
    if response.status_code not in route.allowed_statuses:
        return f"status {response.status_code} is not in {sorted(route.allowed_statuses)}"
    if route.non_empty and not response.body.strip():
        return "body is empty"
    if route.url_fields:
        return _null_url_field(route.url_fields, response.body)
    return None


def _null_url_field(fields: tuple[str, ...], body: str) -> str | None:
    try:
        payload: object = json.loads(body)
    except json.JSONDecodeError:
        return "body is not JSON"
    if not isinstance(payload, dict):
        return "body is not a JSON object"
    for name in fields:
        if payload.get(name) is None:
            return f"url field {name} is null or missing"
    return None


def _check_mutation(client: ApiClient, mutation: Mutation) -> dict[str, Any]:
    label = f"{mutation.method} {mutation.path}"
    path = PATH_PARAMETER.sub(PLACEHOLDER, mutation.path)
    try:
        response = client.request(
            mutation.method, path, headers={}, timeout_seconds=REQUEST_TIMEOUT_SECONDS
        )
    except OSError as exc:
        return _verdict(label, "blocked", f"connection failed ({type(exc).__name__})")
    if response.status_code == MUTATION_STATUS:
        return _verdict(label, "pass")
    reason = (
        f"status {response.status_code} without {DASHBOARD_HEADER}, expected {MUTATION_STATUS}"
    )
    return _verdict(label, "fail", reason)


def _verdict(scenario: str, outcome: str, reason: str | None = None) -> dict[str, Any]:
    verdict: dict[str, Any] = {"layer": LAYER, "scenario": scenario, "verdict": outcome}
    if reason is not None:
        verdict["reason"] = reason
    return verdict
