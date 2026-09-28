# ruff: noqa: E501  (fixture transcripts are kept one message per line)
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from claude_runner.api import create_app
from claude_runner.config import Settings
from claude_runner.dashboard import DashboardSettings, create_dashboard_app
from claude_runner.events import EventLog, StepTracker, entries_from
from claude_runner.sources import hermes_entries
from claude_runner.titles import role_of, title_of

from .conftest import calls, wait_for
from .test_runs import start


def events(client: TestClient, run_id: str, after: int = 0, prefix: str = "/v1") -> Any:
    response = client.get(f"{prefix}/runs/{run_id}/events", params={"after": after})
    assert response.status_code == 200, response.text
    return response.json()


# ------------------------------------------------------------------ claude-runner activity log
def test_run_activity_is_logged(client: TestClient, tmp_path: Path) -> None:
    run = start(client, role="worker", input="TASK T001: fix calc\n[[tools]]", provider="local")
    wait_for(client, run["run_id"])
    body = events(client, run["run_id"])
    kinds = [e["kind"] for e in body["events"]]
    assert kinds == [
        "prompt", "init", "step", "thinking", "tool_use", "tool_result",
        "text", "step", "result", "end",
    ]  # fmt: skip
    by_kind = {e["kind"]: e for e in body["events"]}
    assert by_kind["thinking"]["text"] == "I should read calc.py."
    assert by_kind["tool_use"]["name"] == "Read"
    assert by_kind["tool_use"]["input"] == {"file_path": "calc.py"}
    tool_result = by_kind["tool_result"]
    assert tool_result["id"] == "toolu_1" and "return a + b" in tool_result["text"]
    assert by_kind["end"]["status"] == "completed"
    # steps: the tool round (no token deltas) and the streamed answer (timed)
    first, second = [e for e in body["events"] if e["kind"] == "step"]
    assert first["step"] == 1 and first["input_tokens"] == 50 and "ttft_ms" not in first
    assert second["step"] == 2 and second["cache_read_tokens"] == 1000
    assert second["output_tokens"] == 5 and second["ttft_ms"] >= 0 and second["duration_ms"] >= 0
    assert by_kind["thinking"]["step"] == 1 and by_kind["text"]["step"] == 2
    assert body["next"] == len(kinds) and body["live"] is None
    assert body["run"]["title"] == "TASK T001: fix calc"
    assert body["run"]["provider"] == "local" and body["run"]["source"] == "claude-code"
    assert events(client, run["run_id"], after=8)["events"][0]["kind"] == "result"
    assert "--include-partial-messages" in calls(tmp_path)[0]["args"]


def test_steps_without_token_deltas(settings: Settings) -> None:
    quiet = Settings(**{**settings.__dict__, "live_tokens": False})
    with TestClient(create_app(quiet)) as client:
        run = start(client, input="[[tools]]")
        wait_for(client, run["run_id"])
        steps = [e for e in events(client, run["run_id"])["events"] if e["kind"] == "step"]
        assert [s["step"] for s in steps] == [1, 2]
        assert steps[1]["cache_read_tokens"] == 1000 and "ttft_ms" not in steps[1]


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
    step = next(e for e in body["events"] if e["kind"] == "step")
    assert step["ttft_ms"] < 1500 <= step["duration_ms"] and step["generation_ms"] >= 1500


def test_run_list_filters(client: TestClient) -> None:
    a = start(client, role="architect", input="BEGIN MISSION\nMISSION: Calc\nEND MISSION")
    wait_for(client, a["run_id"])
    w = start(client, role="worker", input="x")
    wait_for(client, w["run_id"])
    runs = client.get("/v1/runs").json()["runs"]
    assert [r["run_id"] for r in runs] == [w["run_id"], a["run_id"]]
    assert runs[1]["title"] == "Plan: Calc" and runs[0]["tokens"]["output"] == 5
    only = client.get("/v1/runs", params={"role": "architect"}).json()["runs"]
    assert [r["run_id"] for r in only] == [a["run_id"]]
    by_session = client.get("/v1/runs", params={"session_id": w["session_id"]}).json()["runs"]
    assert [r["run_id"] for r in by_session] == [w["run_id"]]
    assert client.get("/v1/runs/run_nope/events").status_code == 404


def test_read_views_need_the_bearer(settings: Settings) -> None:
    secured = Settings(**{**settings.__dict__, "api_key": "k"})
    with TestClient(create_app(secured)) as api:
        assert api.get("/v1/runs").status_code == 401
        assert api.get("/v1/runs", headers={"Authorization": "Bearer k"}).status_code == 200


def test_entries_cover_stream_events() -> None:
    retry = {
        "type": "system", "subtype": "api_retry", "attempt": 2, "max_retries": 10,
        "error_status": 529, "error": "overloaded",
    }  # fmt: skip
    assert entries_from(retry) == [
        {"kind": "retry", "attempt": 2, "max_retries": 10, "status": 529,
         "error": "overloaded", "delay_ms": None}
    ]  # fmt: skip
    compact = entries_from(
        {"type": "system", "subtype": "compact_boundary",
         "compact_metadata": {"trigger": "manual", "pre_tokens": 9, "post_tokens": 1}}
    )  # fmt: skip
    assert compact[0]["kind"] == "compact" and compact[0]["post_tokens"] == 1
    sub = entries_from(
        {"type": "assistant", "parent_tool_use_id": "toolu_9",
         "message": {"content": [{"type": "redacted_thinking"}, {"type": "text", "text": "hi"}]}}
    )  # fmt: skip
    assert [e["kind"] for e in sub] == ["thinking", "text"]
    assert all(e["agent"] == "toolu_9" for e in sub)
    big = entries_from(
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t", "name": "Write",
             "input": {"file_path": "a", "content": "x" * 50_000}}]}}
    )  # fmt: skip
    assert len(big[0]["input"]["content"]) < 5_000 and big[0]["input"]["file_path"] == "a"
    result = entries_from(
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t", "is_error": True,
             "content": [{"type": "text", "text": "boom"}, {"type": "image"}]}, "plain"]}}
    )  # fmt: skip
    assert result[0] == {
        "kind": "tool_result",
        "id": "t",
        "is_error": True,
        "text": "boom\n[image]",
    }
    assert result[1] == {"kind": "user", "text": "plain"}
    assert entries_from({"type": "system", "subtype": "status"}) == []


def test_step_tracker_edges() -> None:
    tracker = StepTracker()
    start_event = {"type": "stream_event", "event": {"type": "message_start", "message": {}}}
    assert tracker.feed(start_event, 10.0) == []
    # a request cut off by the next one (or by the result) is still reported
    closed = tracker.feed(start_event, 12.0)
    assert closed[0]["step"] == 1 and closed[0]["duration_ms"] == 2000
    assert tracker.feed({"type": "result"}, 13.0)[0]["step"] == 2
    assert tracker.feed({"type": "stream_event", "event": {"type": "message_stop"}}, 14.0) == []
    sub = {"type": "stream_event", "parent_tool_use_id": "x", "event": start_event["event"]}
    assert tracker.feed(sub, 15.0) == []


def test_event_log_prune_and_bad_ids(tmp_path: Path) -> None:
    import os

    log = EventLog(tmp_path / "ev", retention_days=0)
    old = tmp_path / "ev" / "run_old.jsonl"
    old.write_text('{"seq": 1}\nbroken\n')
    assert log.read("run_old") == ([{"seq": 1}], None)
    assert log.read("../etc/passwd") == ([], None)
    os.utime(old, (0, 0))
    assert log.prune(14) == 1 and not old.exists()


def test_titles_and_roles() -> None:
    assert title_of("prompt\n\nTASK T002: add tests\nOBJECTIVE:") == "TASK T002: add tests"
    assert title_of("REVIEWER\nCURRENT TASK:\nCheck calc\n") == "Review: Check calc"
    assert title_of("Continue the existing Worker task") == "Continue the existing Worker task"
    assert title_of("") == ""
    assert role_of("You are the lead architect and task planner") == "architect"
    assert role_of("Ты локальный coding Worker.") == "worker"
    assert role_of("Ты независимый технический Reviewer") == "reviewer"
    assert role_of("hello") == "hermes"


# ------------------------------------------------------------------ Hermes source
NOW = time.time()
SESSIONS = {
    "w1": {
        "id": "w1", "model": "qwen-local", "started_at": NOW - 100, "last_active": NOW - 5,
        "api_call_count": 2, "input_tokens": 3000, "output_tokens": 120,
        "cache_read_tokens": 1000, "estimated_cost_usd": 0.01, "preview": "Ты локальный coding Worker.",
    },
    "r1": {
        "id": "r1", "model": "opus", "started_at": NOW - 7200, "last_active": NOW - 3600,
        "preview": "Ты независимый Reviewer", "title": "auto title",
    },
}  # fmt: skip
MESSAGES = {
    "w1": [
        {"role": "system", "content": "sys", "timestamp": NOW - 100},
        {"role": "user", "content": "Ты локальный coding Worker.\nTASK T001: Fix calc", "timestamp": NOW - 90},
        {"role": "assistant", "content": "", "reasoning_content": "Look at calc.py", "timestamp": NOW - 80,
         "token_count": 40, "finish_reason": "tool_calls",
         "tool_calls": '[{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\\"path\\": \\"calc.py\\"}"}}]'},
        {"role": "tool", "tool_call_id": "c1", "tool_name": "read_file", "content": '{"content": "def sub(a, b): return a + b", "total_lines": 1}', "timestamp": NOW - 79},
        {"role": "assistant", "content": [{"type": "text", "text": "Fixed."}], "timestamp": NOW - 70, "token_count": 20,
         "tool_calls": [{"id": "c2", "function": {"name": "terminal", "arguments": "not json"}}]},
        {"role": "assistant", "content": "hidden", "display_kind": "hidden", "timestamp": NOW - 60},
    ],
    "r1": [{"role": "user", "content": [{"type": "text", "text": "REVIEWER\nCURRENT TASK:\nFix calc\n"}]}],
}  # fmt: skip


def hermes_handler(request: httpx.Request) -> httpx.Response:
    if request.headers.get("authorization") != "Bearer hk":
        return httpx.Response(401, json={"error": "unauthorized"})
    path = request.url.path
    if path == "/api/sessions":
        assert request.url.params.get("source") == "api_server"
        return httpx.Response(200, json={"object": "list", "data": list(SESSIONS.values())})
    parts = path.split("/")
    if len(parts) == 4 and parts[3] in SESSIONS:
        return httpx.Response(200, json={"session": SESSIONS[parts[3]]})
    if len(parts) == 5 and parts[4] == "messages" and parts[3] in MESSAGES:
        offset = int(request.url.params.get("offset", 0))
        limit = int(request.url.params.get("limit", 500))
        page = MESSAGES[parts[3]][offset : offset + limit]
        return httpx.Response(200, json={"data": page})
    return httpx.Response(404, json={"error": "not found"})


def test_hermes_tool_outcomes() -> None:
    from claude_runner.sources import _tool_outcome

    assert _tool_outcome('{"output": "2", "exit_code": 0, "error": null}') == {
        "is_error": False,
        "preview": "2",
    }
    assert _tool_outcome('{"output": "", "exit_code": 1}') == {"is_error": True}
    assert _tool_outcome('{"error": "no such file"}')["is_error"] is True
    assert _tool_outcome("plain text") == {"is_error": False}
    assert _tool_outcome("[1, 2]") == {"is_error": False}


def test_hermes_entries_steps_tools_and_timing() -> None:
    entries = hermes_entries(MESSAGES["w1"])
    assert [e["kind"] for e in entries] == [
        "prompt",
        "thinking",
        "tool_use",
        "step",
        "tool_result",
        "text",
        "tool_use",
        "step",
    ]
    step1 = entries[3]
    assert step1["step"] == 1 and step1["duration_ms"] == 10_000 and step1["output_tokens"] == 40
    assert step1["tokens_per_s"] == 4.0 and step1["approximate"] is True
    assert entries[2]["input"] == {"path": "calc.py"} and entries[2]["step"] == 1
    assert entries[4]["id"] == "c1" and entries[4]["name"] == "read_file"
    assert entries[4]["preview"] == "def sub(a, b): return a + b" and not entries[4]["is_error"]
    assert entries[6]["input"] == {"arguments": "not json"}
    assert entries[7]["step"] == 2 and entries[7]["started"] == NOW - 79
    assert (
        hermes_entries([{"role": "assistant", "content": "x", "timestamp": 5}])[1]["started"] == 5
    )


def dashboard(runner_app: TestClient, **overrides: Any) -> TestClient:
    """Dashboard wired to a claude-runner test app and the fake Hermes above."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "hermes":
            return hermes_handler(request)
        response = runner_app.get(
            request.url.path, params=dict(request.url.params), headers=dict(request.headers)
        )
        return httpx.Response(
            response.status_code,
            content=response.content,
            headers={"content-type": "application/json"},
        )

    values: dict[str, Any] = {
        "runner_url": "http://claude-runner:8700",
        "hermes_url": "http://hermes:8642",
        "hermes_api_key": "hk",
        "hermes_session_source": "api_server",
        **overrides,
    }
    return TestClient(
        create_dashboard_app(DashboardSettings(**values), transport=httpx.MockTransport(handler)),
        base_url="http://localhost:8701",
    )


def test_dashboard_merges_claude_code_and_hermes(client: TestClient) -> None:
    run = start(client, role="reviewer", input="CURRENT TASK:\nCheck calc\n")
    wait_for(client, run["run_id"])
    with dashboard(client) as dash:
        page = dash.get("/")
        assert page.status_code == 200 and "AHAWR" in page.text
        assert dash.get("/healthz").json()["sources"] == ["claude-runner", "hermes"]
        body = dash.get("/api/runs").json()
        assert body["sources"] == {"claude-runner": "ok", "hermes": "ok"}
        by_id = {r["run_id"]: r for r in body["runs"]}
        worker = by_id["hermes:w1"]
        assert worker["role"] == "worker" and worker["title"] == "TASK T001: Fix calc"
        assert worker["status"] == "running" and worker["source"] == "hermes"
        assert worker["tokens"]["cache_read"] == 1000 and worker["cost_usd"] == 0.01
        reviewer = by_id["hermes:r1"]
        assert reviewer["role"] == "reviewer" and reviewer["status"] == "idle"
        assert reviewer["title"] == "Review: Fix calc"
        assert by_id[run["run_id"]]["source"] == "claude-code"

        only_hermes = dash.get("/api/runs", params={"source": "hermes", "role": "worker"}).json()
        assert [r["run_id"] for r in only_hermes["runs"]] == ["hermes:w1"]
        one = dash.get("/api/runs", params={"session_id": "hermes:r1"}).json()["runs"]
        assert [r["run_id"] for r in one] == ["hermes:r1"]
        mine = dash.get("/api/runs", params={"session_id": run["session_id"]}).json()["runs"]
        assert [r["run_id"] for r in mine] == [run["run_id"]]

        hermes_events = dash.get("/api/runs/hermes:w1/events", params={"after": 2}).json()
        assert hermes_events["next"] == 8 and hermes_events["events"][0]["seq"] == 3
        assert hermes_events["run"]["run_id"] == "hermes:w1"
        claude_events = dash.get(f"/api/runs/{run['run_id']}/events").json()
        assert claude_events["events"][-1]["kind"] == "end"
        assert dash.get("/api/runs/hermes:nope/events").status_code == 404
        assert dash.get("/api/runs/run_nope/events").status_code == 404
        # read-only: nothing to start or change runs with
        assert dash.post("/api/runs", json={"input": "x"}).status_code == 405
        assert dash.post("/v1/runs", json={"input": "x"}).status_code == 404

    with dashboard(client, hermes_api_key="wrong") as dash:
        states = dash.get("/api/runs").json()["sources"]
        assert states == {"claude-runner": "ok", "hermes": "Hermes: 401 (check the API key)"}


def test_dashboard_reports_source_problems() -> None:
    settings = DashboardSettings(hermes_url="http://hermes:8642")

    def failing(request: httpx.Request) -> httpx.Response:
        if request.url.host == "hermes":
            return httpx.Response(500, text="boom")
        return httpx.Response(404, json={})

    app = create_dashboard_app(settings, transport=httpx.MockTransport(failing))
    with TestClient(app, base_url="http://localhost") as dash:
        states = dash.get("/api/runs").json()["sources"]
        assert states["hermes"].startswith("Hermes: HTTP 500")
        assert states["claude-runner"] == "ok"  # a 404 list is simply empty
        assert dash.get("/api/runs/hermes:x/events").status_code == 502

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    app = create_dashboard_app(settings, transport=httpx.MockTransport(offline))
    with TestClient(app, base_url="http://localhost") as dash:
        assert dash.get("/api/runs").json()["sources"]["hermes"] == "unreachable (hermes:8642)"

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    app = create_dashboard_app(settings, transport=httpx.MockTransport(slow))
    with TestClient(app, base_url="http://localhost") as dash:
        assert dash.get("/api/runs").json()["sources"]["hermes"] == "timed out"

    def old_hermes(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={})

    only_hermes = DashboardSettings(runner_url="", hermes_url="http://hermes:8642")
    app = create_dashboard_app(only_hermes, transport=httpx.MockTransport(old_hermes))
    with TestClient(app, base_url="http://localhost") as dash:
        assert "update Hermes" in dash.get("/api/runs").json()["sources"]["hermes"]
        assert dash.get("/api/runs/run_x/events").status_code == 404  # no claude-runner source


def test_dashboard_settings_and_host_guard() -> None:
    settings = DashboardSettings.from_env(
        {"DASHBOARD_HERMES_URL": "http://h:8642", "DASHBOARD_HOSTS": "localhost, dash.lan"}
    )
    assert settings.hermes_url == "http://h:8642" and settings.hosts == ("localhost", "dash.lan")
    assert settings.runner_url == "http://claude-runner:8700"
    with TestClient(create_dashboard_app(settings), base_url="http://evil.example") as dash:
        assert dash.get("/api/runs").status_code == 400


def test_dashboard_main_runs_uvicorn(monkeypatch: Any) -> None:
    import uvicorn

    from claude_runner import dashboard as module

    seen: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    assert module.main(["--port", "9999"]) == 0
    assert seen["port"] == 9999
