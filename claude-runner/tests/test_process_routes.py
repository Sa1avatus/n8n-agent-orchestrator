"""HTTP tests for the process routes (GET /v1/processes, POST /v1/processes/{pid}/terminate).

1. Both routes require the bearer token when ``api_key`` is set (401 without it),
   and a made-up token is accepted.
2. The listing carries only pid, age, the run's session id, run id and status.
3. The CLI environment the runner builds (carrying a made-up secret via the provider
   block) and the command line (which carries it as ``--model``) never reach any
   response field of either route.
4. Terminate is refused with 404 for a pid outside the registry and with 409 for a
   pid whose run is queued or running; it succeeds for the stub process that was
   left in the registry after its run closed.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from claude_runner.api import create_app
from claude_runner.config import ProviderProfile, Settings
from claude_runner.store import now

# A made-up secret the tests set themselves: it lands in the CLI process env
# (the provider block) and in the command line (as ``--model``), and must never
# appear in any API response.
FAKE_SECRET = "test-secret-fake-token"
# The made-up runner token, matching ``api_key="test-api-key"``.
API_KEY = "test-api-key"
AUTH = {"Authorization": f"Bearer {API_KEY}"}

LISTING_FIELDS = ("pid", "age_seconds", "session_id", "run_id", "status")


@pytest.fixture
def secured_settings(settings: Settings, tmp_path: Path) -> Settings:
    s = settings
    object.__setattr__(s, "api_key", API_KEY)
    # A credential-isolating provider block with the made-up token: it is the
    # provider the stub run runs on, so the token reaches the process env and
    # command line of the run that is listed and terminated below.
    object.__setattr__(
        s,
        "providers",
        {
            "FAKEPROVIDER": ProviderProfile(
                name="FAKEPROVIDER", env={"ANTHROPIC_AUTH_TOKEN": FAKE_SECRET}
            )
        },
    )
    return s


def _proc_of(client: TestClient, run_id: str) -> SimpleNamespace:
    """The registry entry of the run's CLI process, once it is tracked."""
    manager = client.app.state.manager
    deadline = time.monotonic() + 10
    while run_id not in manager._procs and time.monotonic() < deadline:
        time.sleep(0.05)
    proc = manager._procs[run_id]
    return SimpleNamespace(pid=proc.pid)


def _start_running(
    client: TestClient,
    model: str = "sonnet",
    provider: str = "",
    auth: dict[str, str] | None = None,
) -> tuple[str, str]:
    payload = {"input": "slow [[sleep:30]]", "model": model}
    if provider:
        payload["provider"] = provider
    response = client.post("/v1/runs", json=payload, headers=auth or {})
    assert response.status_code == 200, response.text
    run = response.json()
    _proc_of(client, str(run["run_id"]))
    return str(run["run_id"]), str(run["session_id"])


def _alive(pid: int) -> bool:
    """Running, not a zombie waiting to be reaped."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            state = fh.read().rsplit(")", 1)[1].split()[0]
    except OSError:
        return False
    return state != "Z"


# --------------------------------------------------------------------------- auth
def test_process_routes_require_auth(settings: Settings) -> None:
    object.__setattr__(settings, "api_key", API_KEY)
    with TestClient(create_app(settings)) as client:
        # Without the token both routes are refused.
        assert client.get("/v1/processes").status_code == 401
        assert client.post("/v1/processes/1/terminate").status_code == 401
        # A wrong token is refused too, as is an empty header (the runner always
        # sends the header; it is empty when no api_key is set).
        assert client.get(
            "/v1/processes", headers={"Authorization": "Bearer not-the-right-token"}
        ).status_code == 401
        assert client.get("/v1/processes", headers={"Authorization": ""}).status_code == 401
        # The made-up token works.
        assert client.get("/v1/processes", headers=AUTH).status_code == 200


def test_process_routes_open_without_api_key(client: TestClient) -> None:
    # No api_key configured: the header (empty, as the runner sends it) is fine.
    assert client.get("/v1/processes", headers={"Authorization": ""}).status_code == 200


# --------------------------------------------------------------------------- listing
def test_listing_fields(client: TestClient) -> None:
    run_id, session_id = _start_running(client)
    body = client.get("/v1/processes").json()
    assert len(body) == 1
    entry = body[0]
    assert set(entry) == set(LISTING_FIELDS)
    assert entry["run_id"] == run_id
    assert entry["session_id"] == session_id
    assert entry["status"] == "running"
    assert isinstance(entry["pid"], int)
    assert entry["age_seconds"] >= 0


def test_listing_and_terminate_hide_secret_and_command(
    client: TestClient, secured_settings: Settings
) -> None:
    # The run runs on the fake provider, so the made-up secret is in the stub's
    # env (provider block) and its command line (--model).
    run_id, session_id = _start_running(
        client, model=FAKE_SECRET, provider="fakeprovider", auth=AUTH
    )
    # The run really ran with the secret as model, so the listing must hide it,
    # not merely miss it.
    assert client.app.state.manager.store.get_run(run_id)["model"] == FAKE_SECRET
    body = client.get("/v1/processes", headers=AUTH).json()
    assert body, "the running stub process must be listed"
    for entry in body:
        for value in entry.values():
            text = str(value)
            assert FAKE_SECRET not in text  # neither env nor command in the listing
            assert "claude" not in text  # no command line
            assert "ANTHROPIC" not in text  # no environment values
    # Terminate while running: refused, and the 409 body carries no env or command.
    entry = body[0]
    denied = client.post(f"/v1/processes/{entry['pid']}/terminate", headers=AUTH)
    assert denied.status_code == 409
    assert FAKE_SECRET not in json.dumps(denied.json())
    # Close the run's row (the stub keeps running, so it lingers in the registry),
    # terminate it, and the listing is empty again without the secret in it.
    manager = client.app.state.manager
    manager.store.update_run(run_id, status="completed", finished_at=now())
    done = client.post(f"/v1/processes/{entry['pid']}/terminate", headers=AUTH)
    assert done.status_code == 200
    assert done.json()["outcome"] == "terminated"
    assert client.get("/v1/processes", headers=AUTH).json() == []


# --------------------------------------------------------------------------- terminate
def test_terminate_unknown_pid(client: TestClient) -> None:
    # A pid that is not in the runner's registry (here: the test process itself)
    # is refused with 404 and no signal is sent to it.
    pid = os.getpid()
    denied = client.post(f"/v1/processes/{pid}/terminate")
    assert denied.status_code == 404
    body = denied.json()
    assert body["code"] == "process_not_found"
    assert body["status"] == "failed"
    assert _alive(pid)


def test_terminate_refused_for_running_and_queued(client: TestClient) -> None:
    manager = client.app.state.manager
    # A run that is running: 409 and the process stays in place.
    run_id, _ = _start_running(client)
    proc = manager._procs[run_id]
    denied = client.post(f"/v1/processes/{proc.pid}/terminate")
    assert denied.status_code == 409
    body = denied.json()
    assert body["code"] == "run_active"
    assert body["status"] == "failed"
    assert "running" in body["error"]["message"]
    assert proc.returncode is None
    # A run that is queued (a registry process with a queued run row): refused too.
    queued_proc = SimpleNamespace(pid=424_242, returncode=None)
    manager._procs["run_queued"] = queued_proc  # type: ignore[assignment]
    manager.store.create_run(
        run_id="run_queued",
        session_id="s-queued",
        claude_session_id="c-queued",
        cwd="/",
        input="x",
    )
    manager.store.bind_session("s-queued", "c-queued", "/", "worker")
    denied = client.post("/v1/processes/424242/terminate")
    assert denied.status_code == 409
    assert denied.json()["code"] == "run_active"
    manager._procs.pop("run_queued", None)
    # The running stub is still there.
    assert proc.returncode is None


def test_terminate_succeeds_for_lingering_stub(client: TestClient) -> None:
    # A run that has closed but whose stub CLI was left in the registry is
    # terminated with 200; the process is killed and the registry entry is gone.
    run_id, _ = _start_running(client)
    manager = client.app.state.manager
    proc = _proc_of(client, run_id)
    # Close the run's row while the stub process is still alive.
    manager.store.update_run(run_id, status="completed", finished_at=now())
    assert client.app.state.manager.store.get_run(run_id)["status"] == "completed"
    assert run_id in manager._procs  # the stub outlived its run
    result = client.post(f"/v1/processes/{proc.pid}/terminate")
    assert result.status_code == 200
    body = result.json()
    assert body["outcome"] == "terminated"
    assert body["pid"] == proc.pid
    assert body["run_id"] == run_id
    deadline = time.monotonic() + 10
    while _alive(proc.pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(proc.pid)
    assert run_id not in manager._procs
    assert client.get("/v1/processes").json() == []
