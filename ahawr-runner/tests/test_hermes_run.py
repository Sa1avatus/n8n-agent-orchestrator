"""Offline tests for the Hermes engine client: served by fake_hermes, no real gateway.

The normal run drives the runner dispatch (start, polls, completed result mapped to entries)
through the API; the follow-up run sends the gateway session id stored by the first run. The
cancel, auth, timeout and unmapped-route tests cover the stop endpoint, the failure codes of
401/403, the bounded poll loop and the fallback entry. URLs and keys are made-up values set
through monkeypatch only; the fake server binds 127.0.0.1 and is shut down in teardown.
The flag-off tests pin the pre-change behaviour: with hermes_engine_enabled still False the
ENGINE=hermes value is rejected by the config and the dispatch stays on the Claude Code path.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ahawr_runner import hermes_run, runs
from ahawr_runner.api import create_app
from ahawr_runner.config import ProviderProfile, Settings, _providers
from ahawr_runner.engine_events import PERMANENT, TRANSIENT
from ahawr_runner.hermes_events import FALLBACK_KIND, classify_failure
from ahawr_runner.store import Store

from .conftest import calls, wait_for
from .fake_hermes import DEFAULT_SCRIPT, FakeHermesServer
from .test_runs import start

FAKE_KEY = "fake-key-value"
FIRST_ANSWER = "fake hermes answer"
SECOND_ANSWER = "follow-up answer"
RUN_2_SCRIPT: list[dict[str, Any]] = [
    {
        "method": "POST",
        "path": "/v1/runs",
        "status": 200,
        "body": {"run_id": "RUN_2", "session_id": "SESSION_1"},
    },
    {
        "method": "GET",
        "path": "/v1/runs/RUN_2",
        "status": 200,
        "body": {
            "status": "completed",
            "output": SECOND_ANSWER,
            "finished_at": "2026-11-03T12:01:00+00:00",
        },
    },
]
FOLLOW_UP_SCRIPT = DEFAULT_SCRIPT + RUN_2_SCRIPT
# The gateway run stays "running": the fake repeats its last entry for that path, so poll_run
# ends at max_polls or at the run deadline, never at a terminal status.
NEVER_DONE_SCRIPT: list[dict[str, Any]] = [
    {
        "method": "POST",
        "path": "/v1/runs",
        "status": 200,
        "body": {"run_id": "RUN_1", "session_id": "SESSION_1"},
    },
    {"method": "GET", "path": "/v1/runs/RUN_1", "status": 200, "body": {"status": "running"}},
]
CANCEL_SCRIPT = NEVER_DONE_SCRIPT + [
    {
        "method": "POST",
        "path": "/v1/runs/RUN_1/cancel",
        "status": 200,
        "body": {"run_id": "RUN_1", "status": "cancelled"},
    },
]
# The fake serves 401 to the first start and 403 to the second one (entries per path in order).
AUTH_SCRIPT: list[dict[str, Any]] = [
    {"method": "POST", "path": "/v1/runs", "status": 401, "body": {"error": "invalid api key"}},
    {"method": "POST", "path": "/v1/runs", "status": 403, "body": {"error": "forbidden"}},
]
#: One run per terminal spelling: poll_run must return on the first GET of each.
TERMINAL_POLL_SCRIPT: list[dict[str, Any]] = [
    {"method": "GET", "path": f"/v1/runs/{name}", "status": 200, "body": {"status": status}}
    for name, status in (
        ("RUN_COMPLETED", "completed"),
        ("RUN_CANCELLED", "cancelled"),
        ("RUN_CANCELED", "canceled"),
    )
]
#: One run per not-yet-finished status: the fake replays the scripted body forever,
#: so poll_run can only end these runs at the max_polls bound, never at a terminal status.
IN_PROGRESS_POLL_SCRIPT: list[dict[str, Any]] = [
    {"method": "GET", "path": f"/v1/runs/{name}", "status": 200, "body": {"status": status}}
    for name, status in (
        ("RUN_RUNNING", "running"),
        ("RUN_STARTED", "started"),
        ("RUN_QUEUED", "queued"),
        ("RUN_APPROVAL", "waiting_for_approval"),
    )
]
UNKNOWN_EVENT_SCRIPT: list[dict[str, Any]] = [
    {
        "method": "POST",
        "path": "/v1/runs",
        "status": 200,
        "body": {"run_id": "RUN_1", "session_id": "SESSION_1"},
    },
    {
        "method": "GET",
        "path": "/v1/runs/RUN_1",
        "status": 200,
        "body": {"status": "failed", "error": "event type the gateway does not name"},
    },
]


@pytest.fixture
def hermes_server(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[FakeHermesServer]:
    server = FakeHermesServer(getattr(request, "param", None))
    monkeypatch.setenv("HERMES_API_URL", server.base_url())
    monkeypatch.setenv("API_SERVER_KEY", FAKE_KEY)
    yield server
    server.shutdown()


@pytest.fixture
def hermes_settings(settings: Settings) -> Settings:
    providers = {
        "HERMESLOCAL": ProviderProfile(
            name="hermeslocal",
            engine="hermes",
            # ms-interval poll keeps the fake run under the 10 s test limit
            hermes_poll_interval_ms=10,
            hermes_run_timeout_ms=10_000,
        ),
    }
    object.__setattr__(settings, "providers", providers)
    object.__setattr__(settings, "hermes_engine_enabled", True)
    return settings


def kinds_of(settings: Settings, run_id: str) -> list[dict[str, object]]:
    lines = (settings.data_dir / "events" / f"{run_id}.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in lines.splitlines()]


def post_bodies(server: FakeHermesServer) -> list[dict[str, Any]]:
    return [r["body"] for r in server.requests if r["method"] == "POST"]


def poll_paths(server: FakeHermesServer) -> list[str]:
    return [r["path"] for r in server.requests if r["method"] == "GET"]


def test_hermes_normal_run_maps_result_to_entries(
    hermes_server: FakeHermesServer, hermes_settings: Settings
) -> None:
    assert hermes_server.host == "127.0.0.1", "the fake gateway binds 127.0.0.1 only"
    with TestClient(create_app(hermes_settings)) as client:
        run = start(client, provider="hermeslocal", role="worker", input="add sub()")
        done = wait_for(client, str(run["run_id"]))
    assert done["status"] == "completed", done
    assert done["output"] == FIRST_ANSWER
    assert done["error"] == ""

    events = kinds_of(hermes_settings, str(run["run_id"]))
    assert events[0]["kind"] == "prompt" and events[0]["resume"] is False
    assert any(e["kind"] == "text" and e["text"] == FIRST_ANSWER for e in events)
    assert events[-1]["kind"] == "end" and events[-1]["status"] == "completed"

    session = Store(hermes_settings.data_dir / "runner.sqlite").get_session(str(run["session_id"]))
    assert session is not None and session["claude_session_id"] == "SESSION_1"

    starts = [r for r in hermes_server.requests if r["method"] == "POST"]
    (start_body,) = post_bodies(hermes_server)
    assert start_body == {
        "model": "sonnet",
        "provider": "hermeslocal",
        "input": "add sub()",
        "working_directory": hermes_settings.workspace,
    }
    assert "session_id" not in start_body
    assert starts[0]["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    # the two polls of RUN_1: still running, then completed
    assert poll_paths(hermes_server) == ["/v1/runs/RUN_1", "/v1/runs/RUN_1"]


@pytest.mark.parametrize("hermes_server", [FOLLOW_UP_SCRIPT], indirect=True)
def test_hermes_follow_up_sends_the_stored_session(
    hermes_server: FakeHermesServer, hermes_settings: Settings
) -> None:
    with TestClient(create_app(hermes_settings)) as client:
        first = start(client, provider="hermeslocal", role="worker", input="add sub()")
        wait_for(client, str(first["run_id"]))
        second = start(
            client,
            provider="hermeslocal",
            role="worker",
            session_id=str(first["session_id"]),
            input="continue",
        )
        done = wait_for(client, str(second["run_id"]))
    assert second["session_created"] is False
    assert done["status"] == "completed", done
    assert done["output"] == SECOND_ANSWER

    second_start = post_bodies(hermes_server)[1]
    assert second_start["session_id"] == "SESSION_1"
    assert second_start["input"] == "continue"
    starts = [r for r in hermes_server.requests if r["method"] == "POST"]
    assert starts[1]["headers"]["X-Hermes-Session-Id"] == "SESSION_1"
    assert "/v1/runs/RUN_2" in poll_paths(hermes_server)
    events = kinds_of(hermes_settings, str(second["run_id"]))
    assert events[0]["kind"] == "prompt" and events[0]["resume"] is True
    assert any(e["kind"] == "text" and e["text"] == SECOND_ANSWER for e in events)


def _profile(
    profile: ProviderProfile, poll_interval_ms: int, max_polls: int
) -> ProviderProfile:
    """The same gateway profile with the poll bounds changed for one test."""
    return replace(
        profile, hermes_poll_interval_ms=poll_interval_ms, hermes_max_polls=max_polls
    )


@pytest.mark.parametrize("hermes_server", [CANCEL_SCRIPT], indirect=True)
def test_hermes_cancel_calls_the_stop_endpoint_and_ends_cancelled(
    hermes_server: FakeHermesServer, hermes_settings: Settings
) -> None:
    profile = _profile(hermes_settings.providers["HERMESLOCAL"], 100, 10)
    object.__setattr__(hermes_settings, "providers", {"HERMESLOCAL": profile})
    with TestClient(create_app(hermes_settings)) as client:
        run = start(client, provider="hermeslocal", role="worker", input="add sub()")
        # bound: wait at most 2 s for the start POST and its first poll (they arrive in ms)
        deadline = time.monotonic() + 2.0
        while not (post_bodies(hermes_server) and poll_paths(hermes_server)):
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        assert poll_paths(hermes_server), "the run was polling when the stop call arrived"
        stop = client.post(f"/v1/runs/{run['run_id']}/cancel")
        assert stop.status_code == 200, stop.text
        done = wait_for(client, str(run["run_id"]))
    assert done["status"] == "cancelled", done
    stop_calls = [
        r for r in hermes_server.requests if r["path"] == "/v1/runs/RUN_1/cancel"
    ]
    assert stop_calls, "the stop endpoint was called on the gateway"
    assert stop_calls[0]["method"] == "POST"
    assert stop_calls[0]["headers"]["Authorization"] == f"Bearer {FAKE_KEY}"
    # the run is still polling when the stop call arrives, and the polls stay bounded
    assert len(poll_paths(hermes_server)) >= 1
    assert len(poll_paths(hermes_server)) <= profile.hermes_max_polls
    events = kinds_of(hermes_settings, str(run["run_id"]))
    assert events[-1]["kind"] == "end" and events[-1]["status"] == "cancelled"


@pytest.mark.parametrize("hermes_server", [AUTH_SCRIPT], indirect=True)
def test_hermes_auth_401_and_403_fail_without_the_key_in_the_error_or_log(
    hermes_server: FakeHermesServer,
    hermes_settings: Settings,
    caplog: pytest.LogCaptureFixture,
) -> None:
    with TestClient(create_app(hermes_settings)) as client:
        runs = [
            start(client, provider="hermeslocal", role="worker", input="add sub()")
            for _ in range(2)
        ]
        done = [wait_for(client, str(run["run_id"])) for run in runs]
    assert [run["http_code"] for run in done] == [401, 403]
    assert all(run["status"] == "failed" for run in done)
    assert classify_failure(done[0]["error"], 401) == (PERMANENT, "auth_error")
    assert classify_failure(done[1]["error"], 403) == (PERMANENT, "auth_error")
    # the key travels in the header, never in the error text, the event log or the runner log
    starts = [r for r in hermes_server.requests if r["method"] == "POST"]
    assert len(starts) == 2
    assert all(r["headers"]["Authorization"] == f"Bearer {FAKE_KEY}" for r in starts)
    for result in done:
        assert FAKE_KEY not in result["error"]["message"]
        log_path = hermes_settings.data_dir / "events" / f"{result['run_id']}.jsonl"
        assert log_path.exists(), "the run wrote its event log"
        assert FAKE_KEY not in log_path.read_text(encoding="utf-8")
    assert FAKE_KEY not in caplog.text


@pytest.mark.parametrize("hermes_server", [NEVER_DONE_SCRIPT], indirect=True)
def test_hermes_timeout_stops_at_max_polls_and_at_the_run_deadline(
    hermes_server: FakeHermesServer, hermes_settings: Settings
) -> None:
    profile = _profile(hermes_settings.providers["HERMESLOCAL"], 5, 4)
    object.__setattr__(hermes_settings, "providers", {"HERMESLOCAL": profile})
    with TestClient(create_app(hermes_settings)) as client:
        run = start(client, provider="hermeslocal", role="worker", input="add sub()")
        done = wait_for(client, str(run["run_id"]))
    assert done["status"] == "failed", done
    assert done["error"]["code"] == "timeout", done
    polls = poll_paths(hermes_server)
    assert len(polls) == profile.hermes_max_polls, "the loop stops exactly at max_polls"
    assert len(polls) <= profile.hermes_max_polls
    assert classify_failure({"route": "timeout"}, 200) == (TRANSIENT, "timeout")
    # the smaller bound wins: a deadline of 40 ms ends the loop before the 500 polls
    tight = replace(profile, hermes_max_polls=500, hermes_run_timeout_ms=40)
    result = hermes_run.poll_run(tight, "RUN_1")
    assert result["route"] == "timeout"
    assert result["poll_count"] < tight.hermes_max_polls
    assert len(poll_paths(hermes_server)) <= profile.hermes_max_polls + tight.hermes_max_polls


@pytest.mark.parametrize("hermes_server", [TERMINAL_POLL_SCRIPT], indirect=True)
def test_poll_run_returns_on_the_first_terminal_status(
    hermes_server: FakeHermesServer, hermes_settings: Settings
) -> None:
    profile = _profile(hermes_settings.providers["HERMESLOCAL"], 1, 3)
    for run_id, status in (
        ("RUN_COMPLETED", "completed"),
        ("RUN_CANCELLED", "cancelled"),
        ("RUN_CANCELED", "canceled"),
    ):
        result = hermes_run.poll_run(profile, run_id)
        assert result["status"] == status, result
        assert result["route"] == status, "the route echoes the finished gateway status"
        assert result["poll_count"] == 1, "the loop stops at the first terminal status"


#: The same script answers every GET with the status it was scripted with: the
#: terminal spelling is echoed by the gateway itself, not by the client, and the
#: loop must not stop early on any of them.
@pytest.mark.parametrize("hermes_server", [IN_PROGRESS_POLL_SCRIPT], indirect=True)
def test_poll_run_keeps_polling_a_not_finished_status_until_max_polls(
    hermes_server: FakeHermesServer, hermes_settings: Settings
) -> None:
    profile = _profile(hermes_settings.providers["HERMESLOCAL"], 1, 3)
    for run_id, status in (
        ("RUN_RUNNING", "running"),
        ("RUN_STARTED", "started"),
        ("RUN_QUEUED", "queued"),
        ("RUN_APPROVAL", "waiting_for_approval"),
    ):
        result = hermes_run.poll_run(profile, run_id)
        assert result["route"] == "timeout", result
        assert result["status"] == status, "the timeout result carries the last non-terminal status"
        assert result["poll_count"] == profile.hermes_max_polls, "the loop is bounded by max_polls"
    polls = poll_paths(hermes_server)
    assert len(polls) == 4 * profile.hermes_max_polls, "every status was polled max_polls times"


@pytest.mark.parametrize("hermes_server", [UNKNOWN_EVENT_SCRIPT], indirect=True)
def test_hermes_unmapped_event_route_gets_the_fallback_notice_entry(
    hermes_server: FakeHermesServer, hermes_settings: Settings
) -> None:
    del hermes_server  # the scripted payload is the interesting part, not the recorded requests
    with TestClient(create_app(hermes_settings)) as client:
        run = start(client, provider="hermeslocal", role="worker", input="add sub()")
        done = wait_for(client, str(run["run_id"]))
    assert done["status"] == "failed", done
    assert done["error"]["code"] == "provider_error", done
    events = kinds_of(hermes_settings, str(run["run_id"]))
    assert any(
        e["kind"] == FALLBACK_KIND and e["text"] == "event type the gateway does not name"
        for e in events
    ), events


_VOLATILE_KEYS = (
    "run_id",
    "session_id",
    "claude_session_id",
    "created_at",
    "started_at",
    "finished_at",
)

# Snapshot of the fake Claude Code fixture's /v1/runs bodies as they are before the hermes
# dispatch: the values test_runs.py::test_start_poll_complete_and_resume asserts for the same run.
FLAG_OFF_POST = json.dumps(
    {
        "api_ms": None,
        "attached": False,
        "claude_session_id": "<claude_session_id>",
        "context_tokens": None,
        "cost_usd": None,
        "created_at": "<created_at>",
        "error": "",
        "finished_at": "<finished_at>",
        "http_code": None,
        "model": "sonnet",
        "num_turns": None,
        "output": "",
        "permission_denials": [],
        "role": "worker",
        "run_id": "<run_id>",
        "session_created": True,
        "session_id": "<session_id>",
        "started_at": "<started_at>",
        "status": "queued",
    },
    sort_keys=True,
)
FLAG_OFF_DONE = json.dumps(
    {
        "api_ms": 1000,
        "claude_session_id": "<claude_session_id>",
        "context_tokens": 1015,
        "cost_usd": 0.001,
        "created_at": "<created_at>",
        "error": "",
        "finished_at": "<finished_at>",
        "http_code": None,
        "model": "sonnet",
        "num_turns": 1,
        "output": "echo[1]: hello",
        "permission_denials": [],
        "role": "worker",
        "run_id": "<run_id>",
        "session_created": True,
        "session_id": "<session_id>",
        "started_at": "<started_at>",
        "status": "completed",
    },
    sort_keys=True,
)


def _frozen_run_view(body: dict[str, Any]) -> str:
    """The run view serialized as JSON, with the per-run ids and timestamps replaced by
    placeholders: every other byte of the response is compared against the snapshot above.
    shortcut: the ids and timestamps are generated per run; pin a fixture with fixed ids if the
    runner ever starts a run with a caller-provided run id."""
    pinned = {
        key: (f"<{key}>" if key in _VOLATILE_KEYS else value) for key, value in body.items()
    }
    return json.dumps(pinned, sort_keys=True)


class _NeverReached:
    """Stand-in for the hermes client: any attribute access raises, so a dispatch that ignored
    the flag would blow up instead of completing the run through the gateway."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"the flag-off dispatch reached the hermes client: {name}")


def test_flag_off_engine_hermes_is_rejected_by_the_config() -> None:
    # With the flag off the accepted list is the pre-existing one, and ENGINE=hermes fails with
    # the same message as any other unknown engine (test_config.py pins the on-flag message).
    with pytest.raises(ValueError) as exc:
        _providers({"CLAUDE_RUNNER_PROVIDER_HERMES__ENGINE": "hermes"})
    assert str(exc.value) == (
        "provider HERMES: ENGINE must be one of claude, got 'hermes'"
    )


def test_flag_off_run_stays_on_the_claude_path_byte_for_byte(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert settings.hermes_engine_enabled is False, "the T004A flag defaults to off"
    monkeypatch.setattr(runs, "hermes_run", _NeverReached())
    object.__setattr__(
        settings,
        "providers",
        {
            "HERMESLOCAL": ProviderProfile(
                name="hermeslocal",
                engine="hermes",
                hermes_poll_interval_ms=10,
                hermes_run_timeout_ms=10_000,
            )
        },
    )
    with TestClient(create_app(settings)) as client:
        run = start(client, provider="hermeslocal", role="worker")
        assert _frozen_run_view(run) == FLAG_OFF_POST, _frozen_run_view(run)
        done = wait_for(client, str(run["run_id"]))
        assert _frozen_run_view(done) == FLAG_OFF_DONE, _frozen_run_view(done)
    cli_calls = calls(tmp_path)
    assert cli_calls and "--session-id" in cli_calls[0]["args"], "the Claude Code CLI ran"
