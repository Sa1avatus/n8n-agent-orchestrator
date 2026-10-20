"""Remote process methods of RemoteRunManager over MockTransport (no network)."""

import httpx

from claude_runner.remote_manager import RemoteRunManager

PROC = {"pid": 123, "age_seconds": 5.0, "session_id": "s1", "run_id": "r1", "status": "completed"}


def test_list_processes_reads_the_bare_list_with_bearer_auth() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["authorization"] == "Bearer test-api-key"
        return httpx.Response(200, json=[PROC])

    m = RemoteRunManager("http://runner", "test-api-key", transport=httpx.MockTransport(handler))
    assert m.list_processes() == [PROC]
    assert len(calls) == 1 and calls[0].url.path == "/v1/processes"


def test_list_processes_handles_refused_and_wrapped_bodies() -> None:
    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    def wrapped(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"processes": [PROC, None]})

    m1 = RemoteRunManager("http://runner", transport=httpx.MockTransport(refused))
    m2 = RemoteRunManager("http://runner", transport=httpx.MockTransport(wrapped))
    assert m1.list_processes() == []
    assert m2.list_processes() == [PROC]


def test_terminate_process_returns_the_outcome_or_none() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["authorization"] == "Bearer test-api-key"
        if request.url.path == "/v1/processes/123/terminate":
            return httpx.Response(200, json={"outcome": "terminated", "pid": 123, "run_id": "r1"})
        return httpx.Response(404, json={"code": "process_not_found"})

    m = RemoteRunManager("http://runner", "test-api-key", transport=httpx.MockTransport(handler))
    assert m.terminate_process(123) == {"outcome": "terminated", "pid": 123, "run_id": "r1"}
    assert m.terminate_process(456) is None
    assert calls[0].url.path == "/v1/processes/123/terminate"
