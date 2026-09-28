from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from claude_runner.api import create_app
from claude_runner.config import Settings
from claude_runner.dashboard import create_dashboard_app, title_of
from claude_runner.events import EventLog, entries_from

from .conftest import calls, wait_for
from .test_runs import start


def events(client: TestClient, run_id: str, after: int = 0, prefix: str = "/v1") -> Any:
    response = client.get(f"{prefix}/runs/{run_id}/events", params={"after": after})
    assert response.status_code == 200, response.text
    return response.json()


def test_run_activity_is_logged(client: TestClient, tmp_path: Path) -> None:
    run = start(client, role="worker", input="TASK T001: fix calc\n[[tools]]", provider="local")
    wait_for(client, run["run_id"])
    body = events(client, run["run_id"])
    kinds = [e["kind"] for e in body["events"]]
    assert kinds == [
        "prompt",
        "init",
        "thinking",
        "tool_use",
        "tool_result",
        "text",
        "result",
        "end",
    ]
    by_kind = {e["kind"]: e for e in body["events"]}
    assert by_kind["thinking"]["text"] == "I should read calc.py."
    assert by_kind["tool_use"]["name"] == "Read"
    assert by_kind["tool_use"]["input"] == {"file_path": "calc.py"}
    assert (
        by_kind["tool_result"]["id"] == "toolu_1"
        and "return a + b" in by_kind["tool_result"]["text"]
    )
    assert by_kind["end"]["status"] == "completed"
    assert [e["seq"] for e in body["events"]] == list(range(1, 9))
    assert body["next"] == 8 and body["live"] is None
    assert body["run"]["title"] == "TASK T001: fix calc"
    assert body["run"]["provider"] == "local" and body["run"]["role"] == "worker"
    # incremental reads, and the log survives a restart (read from disk)
    assert events(client, run["run_id"], after=6)["events"][0]["kind"] == "result"
    assert "--include-partial-messages" in calls(tmp_path)[0]["args"]


def test_live_block_while_generating(client: TestClient) -> None:
    run = start(client, role="worker", input="slow [[stream_sleep:2]]")
    deadline = time.monotonic() + 10
    live = None
    while time.monotonic() < deadline and not live:
        live = events(client, run["run_id"])["live"]
        time.sleep(0.05)
    assert live and live["kind"] == "text" and live["text"] == "ec"
    assert events(client, run["run_id"])["run"]["live"] is True
    wait_for(client, run["run_id"])
    body = events(client, run["run_id"])
    assert body["live"] is None and body["run"]["live"] is False


def test_run_list_filters(client: TestClient) -> None:
    a = start(client, role="architect", input="BEGIN MISSION\nMISSION: Calc\nEND MISSION")
    wait_for(client, a["run_id"])
    w = start(client, role="worker", input="x")
    wait_for(client, w["run_id"])
    runs = client.get("/v1/runs").json()["runs"]
    assert [r["run_id"] for r in runs] == [w["run_id"], a["run_id"]]
    assert runs[1]["title"] == "Plan: Calc"
    only = client.get("/v1/runs", params={"role": "architect"}).json()["runs"]
    assert [r["run_id"] for r in only] == [a["run_id"]]
    by_session = client.get("/v1/runs", params={"session_id": w["session_id"]}).json()["runs"]
    assert [r["run_id"] for r in by_session] == [w["run_id"]]
    assert client.get("/v1/runs/run_nope/events").status_code == 404


def test_dashboard_app_is_read_only_and_host_checked(settings: Settings) -> None:
    main = create_app(settings)
    with TestClient(main) as api:
        run = start(api, role="reviewer", input="CURRENT TASK:\nCheck calc\n")
        wait_for(api, run["run_id"])
        dash = TestClient(
            create_dashboard_app(settings, lambda: main.state.manager),
            base_url="http://localhost:8701",
        )
        page = dash.get("/")
        assert page.status_code == 200 and "AHAWR runs" in page.text
        listed = dash.get("/api/runs").json()["runs"]
        assert listed[0]["title"] == "Review: Check calc"
        assert events(dash, run["run_id"], prefix="/api")["events"][-1]["kind"] == "end"
        # no way to start, cancel or compact runs from the dashboard port
        assert dash.post("/api/runs", json={"input": "x"}).status_code == 405
        assert dash.post("/v1/runs", json={"input": "x"}).status_code == 404
        # DNS-rebinding guard: only configured host names
        evil = TestClient(
            create_dashboard_app(settings, lambda: main.state.manager),
            base_url="http://evil.example:8701",
        )
        assert evil.get("/api/runs").status_code == 400


def test_dashboard_before_startup_and_bearer(settings: Settings) -> None:
    def missing() -> Any:
        raise AttributeError("manager")

    dash = TestClient(create_dashboard_app(settings, missing), base_url="http://localhost")
    assert dash.get("/api/runs").status_code == 503
    secured = Settings(**{**settings.__dict__, "api_key": "k"})
    with TestClient(create_app(secured)) as api:
        assert api.get("/v1/runs").status_code == 401
        ok = api.get("/v1/runs", headers={"Authorization": "Bearer k"})
        assert ok.status_code == 200


def test_entries_cover_stream_events() -> None:
    assert entries_from(
        {
            "type": "system",
            "subtype": "api_retry",
            "attempt": 2,
            "max_retries": 10,
            "error_status": 529,
            "error": "overloaded",
        }
    ) == [
        {
            "kind": "retry",
            "attempt": 2,
            "max_retries": 10,
            "status": 529,
            "error": "overloaded",
            "delay_ms": None,
        }
    ]
    compact = entries_from(
        {
            "type": "system",
            "subtype": "compact_boundary",
            "compact_metadata": {"trigger": "manual", "pre_tokens": 9, "post_tokens": 1},
        }
    )
    assert compact[0]["kind"] == "compact" and compact[0]["post_tokens"] == 1
    sub = entries_from(
        {
            "type": "assistant",
            "parent_tool_use_id": "toolu_9",
            "message": {"content": [{"type": "redacted_thinking"}, {"type": "text", "text": "hi"}]},
        }
    )
    assert [e["kind"] for e in sub] == ["thinking", "text"]
    assert all(e["agent"] == "toolu_9" for e in sub)
    big = entries_from(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t",
                        "name": "Write",
                        "input": {"file_path": "a", "content": "x" * 50_000},
                    }
                ]
            },
        }
    )
    assert len(big[0]["input"]["content"]) < 5_000 and big[0]["input"]["file_path"] == "a"
    result = entries_from(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t",
                        "is_error": True,
                        "content": [{"type": "text", "text": "boom"}, {"type": "image"}],
                    },
                    "plain",
                ]
            },
        }
    )
    assert result[0] == {
        "kind": "tool_result",
        "id": "t",
        "is_error": True,
        "text": "boom\n[image]",
    }
    assert result[1] == {"kind": "user", "text": "plain"}
    assert entries_from({"type": "system", "subtype": "status"}) == []


def test_event_log_prune_and_bad_ids(tmp_path: Path) -> None:
    import os

    log = EventLog(tmp_path / "ev", retention_days=0)
    old = tmp_path / "ev" / "run_old.jsonl"
    old.write_text('{"seq": 1}\nbroken\n')
    assert log.read("run_old") == ([{"seq": 1}], None)
    assert log.read("../etc/passwd") == ([], None)
    os.utime(old, (0, 0))
    assert log.prune(14) == 1 and not old.exists()


def test_titles() -> None:
    assert title_of("prompt\n\nTASK T002: add tests\nOBJECTIVE:") == "TASK T002: add tests"
    assert title_of("Continue the existing Worker task") == "Continue the existing Worker task"
    assert title_of("") == ""


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def test_embedded_dashboard_server(settings: Settings) -> None:
    import socket

    import httpx

    port = _free_port()
    served = Settings(**{**settings.__dict__, "dashboard_port": port})
    with TestClient(create_app(served, serve_dashboard=True)) as api:
        run = start(api, role="worker", input="TASK T9: live")
        wait_for(api, run["run_id"])
        body: Any = None
        for _ in range(100):
            try:
                body = httpx.get(f"http://127.0.0.1:{port}/api/runs", timeout=2).json()
                break
            except httpx.TransportError:
                time.sleep(0.05)
        assert body and body["runs"][0]["title"] == "TASK T9: live"
    # a port that is taken only disables the dashboard
    with socket.socket() as busy:
        busy.bind(("0.0.0.0", 0))
        busy.listen()
        taken = Settings(**{**settings.__dict__, "dashboard_port": busy.getsockname()[1]})
        with TestClient(create_app(taken, serve_dashboard=True)) as api:
            time.sleep(0.3)
            assert api.get("/health").status_code == 200
