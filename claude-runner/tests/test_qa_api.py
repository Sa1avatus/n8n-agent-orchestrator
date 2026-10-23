from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from claude_runner.qa_api import (
    REQUEST_TIMEOUT_SECONDS,
    ApiResponse,
    ApiRoutes,
    GetRoute,
    Mutation,
    check_routes,
    load_routes,
)

ROUTES_FILE = Path(__file__).resolve().parents[1] / "qa" / "api_routes.yaml"
GET_PATH = "/api/board/queue"
MUTATION_PATH = "/api/board/missions/{mission_id}/start"


class FakeClient:
    def __init__(self, handler: Callable[[str, str], ApiResponse]) -> None:
        self.handler = handler
        self.calls: list[tuple[str, str, Mapping[str, str], float]] = []

    def request(
        self, method: str, path: str, *, headers: Mapping[str, str], timeout_seconds: float
    ) -> ApiResponse:
        self.calls.append((method, path, dict(headers), timeout_seconds))
        return self.handler(method, path)


def _routes(url_fields: tuple[str, ...] = ()) -> ApiRoutes:
    return ApiRoutes(
        get_routes=(
            GetRoute(
                path=GET_PATH,
                allowed_statuses=frozenset({200}),
                non_empty=True,
                url_fields=url_fields,
            ),
        ),
        mutations=(Mutation(method="POST", path=MUTATION_PATH),),
    )


def _answer(get: ApiResponse, mutation: ApiResponse | None = None) -> FakeClient:
    def handler(method: str, path: str) -> ApiResponse:
        if method == "GET":
            return get
        return mutation or ApiResponse(403, "")

    return FakeClient(handler)


def _outcomes(verdicts: list[dict[str, Any]]) -> list[str]:
    return [verdict["verdict"] for verdict in verdicts]


def test_all_good_passes() -> None:
    verdicts = check_routes(_answer(ApiResponse(200, '{"items": []}')), _routes())

    assert _outcomes(verdicts) == ["pass", "pass"]
    assert {verdict["layer"] for verdict in verdicts} == {"api"}


def test_server_error_fails() -> None:
    verdicts = check_routes(_answer(ApiResponse(500, "boom")), _routes())

    assert verdicts[0]["verdict"] == "fail"
    assert "server error" in verdicts[0]["reason"]


def test_null_url_field_fails() -> None:
    verdicts = check_routes(_answer(ApiResponse(200, '{"url": null}')), _routes(("url",)))

    assert verdicts[0]["verdict"] == "fail"
    assert "url field url" in verdicts[0]["reason"]


def test_empty_body_fails_when_non_empty_is_set() -> None:
    verdicts = check_routes(_answer(ApiResponse(200, "  \n")), _routes())

    assert verdicts[0]["verdict"] == "fail"
    assert verdicts[0]["reason"] == "body is empty"


def test_mutation_returning_200_fails() -> None:
    verdicts = check_routes(
        _answer(ApiResponse(200, "{}"), mutation=ApiResponse(200, "{}")), _routes()
    )

    assert verdicts[1]["verdict"] == "fail"
    assert verdicts[1]["scenario"] == f"POST {MUTATION_PATH}"
    assert "expected 403" in verdicts[1]["reason"]


def test_connection_error_is_blocked_not_fail() -> None:
    def refuse(method: str, path: str) -> ApiResponse:
        raise ConnectionError("refused")

    verdicts = check_routes(FakeClient(refuse), _routes())

    assert _outcomes(verdicts) == ["blocked", "blocked"]
    assert verdicts[0]["reason"] == "connection failed (ConnectionError)"


def test_blocked_stack_sends_no_request() -> None:
    client = _answer(ApiResponse(200, "{}"))

    verdicts = check_routes(client, _routes(), blocked_reason="stack not ready")

    assert verdicts == [{"layer": "api", "scenario": "api", "verdict": "blocked",
                         "reason": "stack not ready"}]
    assert client.calls == []


def test_requests_use_named_timeout_and_mutations_omit_header() -> None:
    client = _answer(ApiResponse(200, "{}"))

    check_routes(client, _routes())

    assert [call[3] for call in client.calls] == [REQUEST_TIMEOUT_SECONDS] * 2
    assert client.calls[1][1] == "/api/board/missions/qa/start"
    assert all("X-AHAWR-Dashboard" not in call[2] for call in client.calls)


def test_real_routes_file_loads() -> None:
    routes = load_routes(ROUTES_FILE)

    assert [route.path for route in routes.get_routes][0] == "/api/board/missions"
    assert any(mutation.path == "/api/runs/{run_id}/cancel" for mutation in routes.mutations)
