"""RemoteRunManager: the subset of RunManager the board statistics use, over HTTP (MockTransport, no network)."""

import httpx

from claude_runner.remote_manager import RemoteRunManager

RUN = {"run_id": "run_a", "status": "completed", "role": "worker", "provider": "local", "session_id": "s1", "kind": "run",
       "started_at": "2026-10-17T00:00:00+00:00", "finished_at": "2026-10-17T00:30:00+00:00", "cost_usd": 0.0}
STEP = {"seq": 1, "kind": "step", "input_tokens": 10, "output_tokens": 5, "cache_read_tokens": 90, "generation_ms": 250}


def manager(calls: list) -> RemoteRunManager:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers["authorization"] == "Bearer k"
        path = request.url.path
        if path == "/v1/runs/run_a":
            return httpx.Response(200, json={k: v for k, v in RUN.items() if k not in ("provider", "kind")})   # as the real endpoint
        if path == "/v1/runs/run_old":
            return httpx.Response(200, json={"run_id": "run_old", "status": "completed", "role": "worker", "model": "qwen3.8-27b"})
        if path == "/v1/runs/run_old_cloud":
            return httpx.Response(200, json={"run_id": "run_old_cloud", "status": "completed", "role": "reviewer", "model": "claude-sonnet-5-5"})
        if path == "/v1/runs/run_missing":
            return httpx.Response(404, json={"detail": "not found"})
        if path == "/v1/runs/run_a/events":
            after = int(request.url.params["after"])
            events = [STEP] if after == 0 else []
            return httpx.Response(200, json={"run": RUN, "events": events, "next": 1 if events else after})
        if path == "/v1/runs":
            return httpx.Response(200, json={"runs": [RUN, None]})
        return httpx.Response(404)

    return RemoteRunManager("http://runner", "k", transport=httpx.MockTransport(handler))


def test_get_run_and_missing_run():
    calls: list = []
    m = manager(calls)
    assert m.store.get_run("run_a")["role"] == "worker"
    assert m.store.get_run("run_missing") is None
    m.store.get_run("run_a")
    assert sum("/v1/runs/run_a" in c and "events" not in c for c in calls) == 1  # a finished run is cached


def test_events_read_pages_until_empty_and_are_cached_when_finished():
    calls: list = []
    m = manager(calls)
    entries, cursor = m.events.read("run_a", 0)
    assert entries == [STEP] and cursor == 1
    n = len(calls)
    assert m.events.read("run_a", 0)[0] == [STEP]
    assert len(calls) == n


def test_list_runs_drops_empty_rows_and_pricing_is_off():
    m = manager([])
    assert [r["run_id"] for r in m.store.list_runs(10, "s1")] == ["run_a"]
    assert m.local_pricing.enabled is False


def test_an_unreachable_runner_gives_empty_results():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    m = RemoteRunManager("http://runner", transport=httpx.MockTransport(boom))
    assert m.store.get_run("x") is None
    assert m.events.read("x", 0) == ([], 0)
    assert m.store.list_runs(5) == []


def test_get_run_has_the_provider_although_the_single_run_endpoint_has_none():
    """The board speed and the real cost need the provider: it comes from the list row, or is guessed from the model name."""
    m = manager([])
    run = m.store.get_run("run_a")
    assert run["provider"] == "local" and run["kind"] == "run" and run["cost_usd"] == 0.0
    assert m.store.get_run("run_old")["provider"] == "local"
    assert m.store.get_run("run_old_cloud")["provider"] == "anthropic"


def test_the_list_used_for_the_missing_fields_is_read_once_in_a_while():
    calls: list = []
    m = manager(calls)
    m.store.get_run("run_a")
    m.store.get_run("run_old")
    m.store.get_run("run_old_cloud")
    assert sum(c.endswith("/v1/runs?limit=500") for c in calls) == 1
